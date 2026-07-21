import SwiftUI

/// The Connect surface, opened from the header status pill or the empty state. USB
/// only for now: it lists the known readers present on the bus, lets the user RESCAN,
/// and manually pin a serial port when auto-detect does not recognise the device (the
/// Chameleon-over-USB case). Bluetooth is a deliberately DISABLED placeholder for a
/// later pass. Instrument aesthetic: hairlines, muted tokens, mono for machine
/// identifiers (port paths), sans for chrome, signal via glyph + weight, no alarm colour.
struct ConnectView: View {
    @Environment(AppModel.self) private var model
    @Environment(Theme.self) private var theme
    @Environment(L10n.self) private var l
    @Environment(\.dismiss) private var dismiss
    @State private var manualPort = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            divider
            detectedSection
            divider
            manualSection
            if let hint = diagnosticHint {
                divider
                diagnostics(hint)
            }
            divider
            bluetoothRow
        }
        .frame(width: 340)
        .background(theme.p.panel)
        .onAppear { model.refreshConnectLists() }
    }

    // MARK: - header

    private var header: some View {
        HStack(spacing: 8) {
            Text(l.t("connect")).font(l.sans(13, .semibold)).foregroundStyle(theme.p.textPrimary)
            Spacer()
            if model.connecting { ProgressView().controlSize(.small) }
            Button { Task { await model.rescan() } } label: {
                HStack(spacing: 5) {
                    Image(systemName: "arrow.clockwise").font(.system(size: 10))
                    Text(l.t("rescan")).font(l.sans(11, .medium))
                }
                .padding(.horizontal, 9).frame(height: 26)
                .background(RoundedRectangle(cornerRadius: 6).fill(theme.p.tileFill.opacity(0.6)))
                .overlay(RoundedRectangle(cornerRadius: 6).strokeBorder(theme.p.tileBorder, lineWidth: 0.5))
                .foregroundStyle(theme.p.textPrimary)
            }
            .buttonStyle(.plain).disabled(model.connecting || !model.canChangeDevice).help(l.t("rescan"))
        }
        .padding(.horizontal, 16).padding(.vertical, 12)
    }

    // MARK: - detected devices

    private var detectedSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            sectionLabel(l.t("device"))
            if model.detectedDevices.isEmpty {
                Text(l.t("no_reader_detected")).font(l.sans(11)).foregroundStyle(theme.p.textTertiary)
            } else {
                ForEach(model.detectedDevices) { deviceRow($0) }
                Text(l.t("auto_connect_note")).font(l.sans(10)).foregroundStyle(theme.p.textTertiary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(.horizontal, 16).padding(.vertical, 12)
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func deviceRow(_ d: DeviceDescriptor) -> some View {
        HStack(spacing: 9) {
            Image(systemName: "cable.connector").font(.system(size: 12))
                .foregroundStyle(theme.p.textSecondary).frame(width: 16)
            Text(d.displayName).font(l.sans(12)).foregroundStyle(theme.p.textPrimary)
            if d.family == "chameleon-dfu" { tag("DFU") }
            Spacer()
            if isActive(d) { Circle().fill(theme.p.accent).frame(width: 6, height: 6) }
        }
    }

    private func isActive(_ d: DeviceDescriptor) -> Bool {
        d.family == model.activeDeviceFamily && (model.readerOnline || model.deviceInDFU)
    }

    // MARK: - manual connect (serial ports)

    private var manualSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            sectionLabel(l.t("serial_ports"))
            if model.serialPorts.isEmpty {
                Text(l.t("no_ports")).font(l.sans(11)).foregroundStyle(theme.p.textTertiary)
            } else {
                ForEach(model.serialPorts) { portRow($0) }
            }
            HStack(spacing: 6) {
                TextField(l.t("enter_port"), text: $manualPort)
                    .textFieldStyle(.plain).font(Typeface.mono(11))
                    .foregroundStyle(theme.p.textPrimary)
                    .padding(.horizontal, 8).frame(height: 26)
                    .background(RoundedRectangle(cornerRadius: 6).fill(theme.p.tileFill.opacity(0.6)))
                    .overlay(RoundedRectangle(cornerRadius: 6).strokeBorder(theme.p.tileBorder, lineWidth: 0.5))
                    .onSubmit(submitManual)
                Button(l.t("connect_action"), action: submitManual)
                    .buttonStyle(.plain).font(l.sans(11, .medium))
                    .foregroundStyle(manualTrimmed.isEmpty ? theme.p.textTertiary : theme.p.accent)
                    .disabled(manualTrimmed.isEmpty || !model.canChangeDevice)
            }
        }
        .padding(.horizontal, 16).padding(.vertical, 12)
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func portRow(_ port: SerialPortInfo) -> some View {
        Button {
            guard !port.isDFU, model.canChangeDevice else { return }
            Task { await model.connectManual(port: port.path) }
            dismiss()
        } label: {
            HStack(spacing: 9) {
                Image(systemName: "cable.connector").font(.system(size: 12))
                    .foregroundStyle(theme.p.textSecondary).frame(width: 16)
                VStack(alignment: .leading, spacing: 2) {
                    Text(port.path).font(Typeface.mono(11)).foregroundStyle(theme.p.textPrimary).lineLimit(1)
                    if let sub = portSubtitle(port) {
                        Text(sub).font(l.sans(10)).foregroundStyle(theme.p.textTertiary).lineLimit(1)
                    }
                }
                Spacer()
                if port.isDFU {
                    tag("DFU")
                } else if port.isChameleon {
                    Text(l.t("likely_chameleon")).font(l.sans(9)).foregroundStyle(theme.p.accent)
                }
            }
            .padding(.horizontal, 10).frame(minHeight: 36)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(RoundedRectangle(cornerRadius: 7).fill(theme.p.tileFill.opacity(port.isDFU ? 0.3 : 0.6)))
            .overlay(RoundedRectangle(cornerRadius: 7).strokeBorder(theme.p.tileBorder, lineWidth: 0.5))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain).disabled(port.isDFU || !model.canChangeDevice)
        .help(port.isDFU ? l.t("dfu_recover_hint") : l.t("manual_connect"))
    }

    private func portSubtitle(_ port: SerialPortInfo) -> String? {
        let parts = [port.vendorName, port.productName].compactMap { $0 }.filter { !$0.isEmpty }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }

    // MARK: - diagnostics + bluetooth placeholder

    private func diagnostics(_ hint: String) -> some View {
        HStack(spacing: 7) {
            Image(systemName: "info.circle").font(.system(size: 10)).foregroundStyle(theme.p.textTertiary)
            Text(hint).font(l.sans(10)).foregroundStyle(theme.p.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 16).padding(.vertical, 10)
    }

    /// Honest guidance below the lists: recognisable readers absent but serial ports
    /// present -> connect one manually; nothing at all -> check the cable / usb port.
    private var diagnosticHint: String? {
        guard model.detectedDevices.isEmpty else { return nil }
        return model.serialPorts.isEmpty ? l.t("no_device_hint") : l.t("unrecognized_port_hint")
    }

    private var bluetoothRow: some View {
        HStack(spacing: 9) {
            Image(systemName: "dot.radiowaves.left.and.right").font(.system(size: 12))
                .foregroundStyle(theme.p.textTertiary).frame(width: 16)
            VStack(alignment: .leading, spacing: 2) {
                Text(l.t("bluetooth")).font(l.sans(12)).foregroundStyle(theme.p.textTertiary)
                Text(l.t("bluetooth_later")).font(l.sans(10)).foregroundStyle(theme.p.textTertiary)
            }
            Spacer()
        }
        .padding(.horizontal, 16).padding(.vertical, 12)
        .opacity(0.7)
    }

    // MARK: - shared

    private var divider: some View { Rectangle().fill(theme.p.hairline).frame(height: 1) }

    private func sectionLabel(_ text: String) -> some View {
        Text(text).font(l.sans(9)).tracking(0.8).foregroundStyle(theme.p.textTertiary)
    }

    private func tag(_ text: String) -> some View {
        Text(text).font(Typeface.mono(9)).foregroundStyle(theme.p.textSecondary)
            .padding(.horizontal, 5).padding(.vertical, 1)
            .background(RoundedRectangle(cornerRadius: 4).fill(theme.p.tileFill))
            .overlay(RoundedRectangle(cornerRadius: 4).strokeBorder(theme.p.tileBorder, lineWidth: 0.5))
    }

    private var manualTrimmed: String { manualPort.trimmingCharacters(in: .whitespaces) }

    private func submitManual() {
        let p = manualTrimmed
        guard !p.isEmpty, model.canChangeDevice else { return }
        Task { await model.connectManual(port: p) }
        manualPort = ""
        dismiss()
    }
}
