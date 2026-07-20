import SwiftUI

/// Firmware update (DFU) as a native attached sheet, shown only when the connected device
/// advertises it (capabilities.dfu; the X7 never does). v1 is DOWNLOAD-ONLY: it reads the
/// running firmware + the newest official release and flashes that model-specific asset.
/// There is no local-file picker (removed to eliminate the malicious-input surface). When
/// the device is stuck in the bootloader it presents an Ultra/Lite choice (its model cannot
/// be read there) with a confirm. The daemon validates the download (app-only, hash) before
/// writing anything and refuses a mid-write cancel, so this is a commit-once action: the
/// safety messaging is prominent and the sheet cannot be dismissed while a flash is in flight.
struct FlashingView: View {
    @Environment(AppModel.self) private var model
    @Environment(Theme.self) private var theme
    @Environment(L10n.self) private var l
    @Environment(\.dismiss) private var dismiss

    /// The model the user picked to recover a stuck-in-DFU device ("ultra"/"lite"); drives
    /// the confirm dialog (flashing the wrong model can brick, so it is confirmed).
    @State private var confirmModel: String?
    @State private var confirmingModel = false

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text(l.t("firmware_update")).font(l.sans(14, .medium))
                .foregroundStyle(theme.p.textPrimary)

            versionPanel
            safetyNote

            if model.flashing {
                flashingState
            } else if model.flashDone {
                doneState
            } else {
                if model.flashError != nil { failBanner }
                idleState
            }

            manualNote
        }
        .padding(22)
        .frame(width: 480)
        .background(theme.p.panel)
        .interactiveDismissDisabled(model.flashing)
        .confirmationDialog(l.t("recover_confirm_title"), isPresented: $confirmingModel, titleVisibility: .visible) {
            Button(l.t("firmware"), role: .destructive) {
                let m = confirmModel
                Task { await model.flashFirmware(model: m) }
            }
            Button(l.t("cancel"), role: .cancel) {}
        } message: {
            Text(l.t("recover_confirm_msg") + (confirmModel.map { "\n\n\($0)" } ?? ""))
        }
    }

    // ---- version panel: model + running firmware + latest release --------------

    private var versionPanel: some View {
        VStack(alignment: .leading, spacing: 10) {
            row(l.t("device"), model.dfuStatus?.model ?? model.info?.model ?? "-")
            row(l.t("firmware_current"), currentText)
            HStack(spacing: 8) {
                labelText(l.t("firmware_latest"))
                if let latest = model.dfuStatus?.latest {
                    Text(latest).font(Typeface.mono(12)).foregroundStyle(theme.p.textPrimary)
                    if model.dfuStatus?.updateAvailable == true {
                        Text(l.t("update_available")).font(l.sans(9, .medium))
                            .foregroundStyle(theme.p.accentText)
                            .padding(.horizontal, 7).padding(.vertical, 2)
                            .background(Capsule().fill(theme.p.accent))
                    }
                } else if model.flashing || model.deviceInDFU {
                    Text("-").font(Typeface.mono(12)).foregroundStyle(theme.p.textTertiary)
                } else {
                    HStack(spacing: 6) {
                        ProgressView().controlSize(.small)
                        Text(l.t("checking_updates")).font(l.sans(11)).foregroundStyle(theme.p.textTertiary)
                    }
                }
                Spacer()
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: TenorRadius.md).fill(theme.p.tileFill))
        .overlay(RoundedRectangle(cornerRadius: TenorRadius.md).strokeBorder(theme.p.hairline, lineWidth: 0.5))
    }

    private var currentText: String {
        if let s = model.dfuStatus { return "\(s.current)  (\(s.git))" }
        return model.info?.hw ?? "-"
    }

    private func row(_ label: String, _ value: String) -> some View {
        HStack(spacing: 8) {
            labelText(label)
            Text(value).font(Typeface.mono(12)).foregroundStyle(theme.p.textPrimary).lineLimit(1)
            Spacer()
        }
    }

    private func labelText(_ s: String) -> some View {
        Text(s).font(l.sans(9)).tracking(0.8).foregroundStyle(theme.p.textTertiary).frame(width: 84, alignment: .leading)
    }

    // ---- states ---------------------------------------------------------------

    /// Idle. In normal mode: "update to latest" (download-only) + re-check + close. In the
    /// bootloader: the model cannot be read, so present an explicit Ultra/Lite recovery choice
    /// (each confirmed) instead - never guess the model.
    private var idleState: some View {
        VStack(alignment: .leading, spacing: 14) {
            if model.deviceInDFU {
                Text(l.t("dfu_recover_hint")).font(l.sans(10)).foregroundStyle(theme.p.textTertiary)
                    .fixedSize(horizontal: false, vertical: true)
                HStack {
                    Spacer()
                    Button(l.t("done")) { dismiss() }.keyboardShortcut(.cancelAction)
                    Button("\(l.t("recover_as")) Ultra") { confirmModel = "ultra"; confirmingModel = true }
                        .buttonStyle(.bordered).tint(theme.p.accent)
                    Button("\(l.t("recover_as")) Lite") { confirmModel = "lite"; confirmingModel = true }
                        .buttonStyle(.borderedProminent).tint(theme.p.accent)
                }
            } else {
                HStack {
                    Button(l.t("check_again")) { Task { await model.checkFirmware() } }
                        .buttonStyle(.plain).font(l.sans(11)).foregroundStyle(theme.p.accent)
                    Spacer()
                    Button(l.t("done")) { dismiss() }.keyboardShortcut(.cancelAction)
                    Button(l.t("update_latest")) { Task { await model.flashFirmware(model: nil) } }
                        .keyboardShortcut(.defaultAction)
                        .buttonStyle(.borderedProminent).tint(theme.p.accent)
                        .disabled(model.dfuStatus?.latest == nil)
                }
                if model.dfuStatus?.latest == nil, let note = model.dfuStatus?.note {
                    Text("\(l.t("offline_note")) \(note)").font(l.sans(10)).foregroundStyle(theme.p.textTertiary)
                }
            }
        }
    }

    /// A failed flash, shown in the sheet with the recovery path (the device is usually
    /// left in the bootloader, so the retry actions below act on it directly).
    private var failBanner: some View {
        HStack(alignment: .top, spacing: 8) {
            Image(systemName: "exclamationmark.triangle").font(.system(size: 11))
                .foregroundStyle(theme.p.textPrimary)
            VStack(alignment: .leading, spacing: 3) {
                Text(model.flashError ?? l.t("firmware_failed")).font(l.sans(11))
                    .foregroundStyle(theme.p.textSecondary).lineLimit(3)
                Text(l.t("firmware_retry_hint")).font(l.sans(10)).foregroundStyle(theme.p.textTertiary)
            }
            Spacer()
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: TenorRadius.sm).fill(theme.p.tileFill))
        .overlay(RoundedRectangle(cornerRadius: TenorRadius.sm)
            .strokeBorder(theme.p.textTertiary, style: StrokeStyle(lineWidth: 1, dash: [3, 2])))
    }

    /// While a flash runs: the phase + a determinate bar (percent) or an indeterminate
    /// one, and the do-not-unplug warning reinforced. No cancel - the daemon refuses a
    /// mid-write abort (that can brick), so the button is intentionally absent.
    private var flashingState: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 10) {
                ProgressView().controlSize(.small)
                Text(stageText).font(l.sans(12)).foregroundStyle(theme.p.textSecondary)
                Spacer()
            }
            if let pct = model.flashPercent, model.flashStage == "flash" || model.flashStage == "download" {
                ProgressView(value: Double(pct), total: 100).tint(theme.p.accent)
            } else {
                ProgressView().progressViewStyle(.linear).tint(theme.p.accent)
            }
        }
    }

    /// After a successful flash: the device reboots into the new firmware; the header /
    /// status monitor picks up the new version on its next poll.
    private var doneState: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(spacing: 8) {
                Image(systemName: "checkmark.circle").font(.system(size: 12)).foregroundStyle(theme.p.textPrimary)
                Text(l.t("firmware_done")).font(l.sans(12)).foregroundStyle(theme.p.textSecondary)
                Spacer()
            }
            Text(l.t("firmware_reboot")).font(l.sans(10)).foregroundStyle(theme.p.textTertiary)
            HStack {
                Spacer()
                Button(l.t("done")) { dismiss() }
                    .keyboardShortcut(.defaultAction)
                    .buttonStyle(.borderedProminent).tint(theme.p.accent)
            }
        }
    }

    // ---- always-on notes -------------------------------------------------------

    /// The primary safety line: never unplug during an update. Instrument discipline -
    /// glyph + structure carry the warning, no alarm colour.
    private var safetyNote: some View {
        HStack(alignment: .top, spacing: 8) {
            Image(systemName: "exclamationmark.triangle").font(.system(size: 11))
                .foregroundStyle(theme.p.textPrimary)
            Text(l.t("firmware_warning")).font(l.sans(10)).foregroundStyle(theme.p.textSecondary)
            Spacer()
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: TenorRadius.sm).fill(theme.p.tileFill))
        .overlay(RoundedRectangle(cornerRadius: TenorRadius.sm)
            .strokeBorder(theme.p.textTertiary, style: StrokeStyle(lineWidth: 1, dash: [3, 2])))
    }

    /// The manual recovery path if the device will not enter DFU on its own (already
    /// crashed): power off, hold B, plug in. Always shown so it is there when needed.
    private var manualNote: some View {
        Text(l.t("firmware_manual")).font(l.sans(10)).foregroundStyle(theme.p.textTertiary)
            .fixedSize(horizontal: false, vertical: true)
    }

    /// Map the daemon's flash phase to a readable line (with the percent when flashing).
    private var stageText: String {
        switch model.flashStage {
        case "download": return l.t("stage_download") + (model.flashPercent.map { " \($0)%" } ?? "")
        case "prepare", "validated": return l.t("stage_validate")
        case "enter": return l.t("stage_enter")
        case "wait": return l.t("stage_wait")
        case "flash": return l.t("stage_flash") + (model.flashPercent.map { " \($0)%" } ?? "")
        case "done": return l.t("firmware_done")
        default: return l.t("stage_validate")
        }
    }
}
