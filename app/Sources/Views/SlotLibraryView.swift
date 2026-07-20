import SwiftUI

/// The Chameleon's 8-slot library, shown as a Chameleon-only detail area when the
/// device advertises slots (capabilities.slots > 0). Each slot tile reads the same
/// calm grammar as the sector grid: a solid tile when populated, a dashed void when
/// empty, the active slot marked. Selecting a slot opens its actions below (make
/// active, enable/disable a field, set type, rename, save to flash) and, for an HF
/// MIFARE Classic slot, opens its emulator content into the working document so the
/// existing sector grid / inspector render it.
struct SlotLibraryView: View {
    @Environment(AppModel.self) private var model
    @Environment(Theme.self) private var theme
    @Environment(L10n.self) private var l

    private let cols = Array(repeating: GridItem(.flexible(), spacing: 12), count: 4)

    var body: some View {
        VStack(spacing: 0) {
            header
            Rectangle().fill(theme.p.hairline).frame(height: 1)
            ScrollView {
                LazyVGrid(columns: cols, spacing: 12) {
                    ForEach(model.slots) { slot in
                        SlotTile(slot: slot, selected: model.selectedSlot == slot.index)
                            .onTapGesture {
                                withAnimation(.easeOut(duration: 0.16)) { model.selectedSlot = slot.index }
                            }
                    }
                }
                .padding(24)
            }
            if let sel = model.selectedSlot, let slot = model.slots.first(where: { $0.index == sel }) {
                Rectangle().fill(theme.p.hairline).frame(height: 1)
                SlotDetail(slot: slot)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .task { if model.slots.isEmpty { await model.loadSlots() } }
    }

    private var header: some View {
        HStack(spacing: 12) {
            Text(l.t("slot_library")).font(l.sans(13, .medium)).foregroundStyle(theme.p.textPrimary)
            if model.slotBusy { ProgressView().controlSize(.small) }
            Spacer()
            ActionButton(title: l.t("save_slots"), icon: "internaldrive",
                         enabled: !model.slotBusy) { Task { await model.saveSlots() } }
        }
        .padding(.horizontal, 24).padding(.vertical, 12)
        .background(theme.p.panel)
    }
}

/// One slot tile: number, active mark, and its HF / LF fields (type + nick + enabled).
private struct SlotTile: View {
    let slot: ChameleonSlot
    let selected: Bool
    @Environment(Theme.self) private var theme
    @Environment(L10n.self) private var l

    private var populated: Bool { slot.hf.type != "UNDEFINED" || slot.lf.type != "UNDEFINED" }

    var body: some View {
        ZStack(alignment: .topLeading) {
            RoundedRectangle(cornerRadius: 7)
                .fill(populated ? theme.p.tileFill : Color.clear)
                .overlay(
                    RoundedRectangle(cornerRadius: 7).strokeBorder(
                        border,
                        style: populated
                            ? StrokeStyle(lineWidth: selected ? 1.2 : 0.5)
                            : StrokeStyle(lineWidth: 1, dash: [3, 3])))
            VStack(alignment: .leading, spacing: 0) {
                HStack(spacing: 5) {
                    Text(String(format: "s%02d", slot.index + 1))
                        .font(Typeface.mono(10))
                        .foregroundStyle(populated ? theme.p.textSecondary : theme.p.textTertiary)
                    Spacer()
                    if slot.active {
                        Circle().fill(theme.p.accent).frame(width: 6, height: 6)
                    }
                }
                Spacer()
                fieldLines
            }
            .padding(8)
        }
        .frame(height: 92)
        .contentShape(Rectangle())
    }

    private var border: Color {
        if selected { return theme.p.accent }
        return populated ? theme.p.tileBorder : theme.p.voidStroke
    }

    @ViewBuilder private var fieldLines: some View {
        VStack(alignment: .leading, spacing: 3) {
            if slot.hf.type != "UNDEFINED" {
                fieldRow(tag: "hf", sense: slot.hf)
            }
            if slot.lf.type != "UNDEFINED" {
                fieldRow(tag: "lf", sense: slot.lf)
            }
            if !populated {
                Text(l.t("empty_slot")).font(l.sans(9)).foregroundStyle(theme.p.textTertiary)
            } else if !slot.hf.nick.isEmpty {
                Text(slot.hf.nick).font(l.sans(9)).foregroundStyle(theme.p.textSecondary).lineLimit(1)
            }
        }
    }

    private func fieldRow(tag: String, sense: SlotSense) -> some View {
        HStack(spacing: 5) {
            Text(tag).font(Typeface.mono(8)).foregroundStyle(theme.p.textTertiary)
            Text(slotTypeLabel(sense.type)).font(Typeface.mono(9))
                .foregroundStyle(sense.enabled ? theme.p.textSecondary : theme.p.textTertiary)
                .lineLimit(1)
            Spacer(minLength: 2)
            if sense.enabled {
                Image(systemName: "checkmark").font(.system(size: 7, weight: .bold))
                    .foregroundStyle(theme.p.accent)
            }
        }
    }
}

/// Actions for the selected slot: make active, enable/disable the HF field, set the
/// emulated type, rename, and (for an HF MIFARE Classic slot) open its content into
/// the working document. Kept to the calm action-bar grammar (labelled verbs).
private struct SlotDetail: View {
    let slot: ChameleonSlot
    @Environment(AppModel.self) private var model
    @Environment(Theme.self) private var theme
    @Environment(L10n.self) private var l
    @State private var nick = ""

    private var busy: Bool { model.slotBusy }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 8) {
                ActionButton(title: l.t("make_active"), icon: "checkmark.circle",
                             enabled: !slot.active && !busy) { Task { await model.selectSlot(slot.index) } }
                ActionButton(title: l.t(slot.hf.enabled ? "disable" : "enable"), icon: "power",
                             enabled: !busy) {
                    Task { await model.enableSlot(slot.index, sense: "hf", enabled: !slot.hf.enabled) }
                }
                if slot.hfIsClassic {
                    ActionButton(title: l.t("open_content"), icon: "square.grid.3x3",
                                 enabled: !busy) { Task { await model.openSlotContent(slot.index) } }
                }
                Spacer()
            }
            HStack(spacing: 8) {
                Menu {
                    ForEach(SlotTagType.selectable) { t in
                        Button(t.label) { Task { await model.setSlotType(slot.index, type: t.name) } }
                    }
                } label: {
                    HStack(spacing: 6) {
                        Image(systemName: "tag").font(.system(size: 11))
                        Text(l.t("set_type")).font(l.sans(12, .medium))
                    }
                    .padding(.horizontal, 11).frame(height: 30)
                    .background(RoundedRectangle(cornerRadius: 7).fill(theme.p.tileFill.opacity(0.6)))
                    .overlay(RoundedRectangle(cornerRadius: 7).strokeBorder(theme.p.tileBorder, lineWidth: 0.5))
                    .foregroundStyle(theme.p.textPrimary)
                }
                .menuStyle(.borderlessButton).fixedSize().disabled(busy)

                TextField(l.t("slot_name"), text: $nick)
                    .textFieldStyle(.roundedBorder).font(Typeface.mono(12)).frame(maxWidth: 200)
                    .onSubmit { rename() }
                ActionButton(title: l.t("rename"), icon: "pencil", enabled: !busy) { rename() }
                Spacer()
            }
            // LF field controls, shown only when the device can drive LF (capabilities.lf).
            // A slot's LF field can be set to EM410x (the only LF emulate type in v1) and
            // enabled / disabled; the id itself is loaded from the LF panel (lf_emu).
            if model.capabilities.lf {
                HStack(spacing: 8) {
                    Menu {
                        ForEach(SlotTagType.lf) { t in
                            Button(t.label) { Task { await model.setSlotType(slot.index, type: t.name) } }
                        }
                    } label: {
                        HStack(spacing: 6) {
                            Image(systemName: "wifi").font(.system(size: 11))
                            Text(l.t("set_lf_type")).font(l.sans(12, .medium))
                        }
                        .padding(.horizontal, 11).frame(height: 30)
                        .background(RoundedRectangle(cornerRadius: 7).fill(theme.p.tileFill.opacity(0.6)))
                        .overlay(RoundedRectangle(cornerRadius: 7).strokeBorder(theme.p.tileBorder, lineWidth: 0.5))
                        .foregroundStyle(theme.p.textPrimary)
                    }
                    .menuStyle(.borderlessButton).fixedSize().disabled(busy)
                    ActionButton(title: l.t(slot.lf.enabled ? "disable_lf" : "enable_lf"), icon: "power",
                                 enabled: !busy) {
                        Task { await model.enableSlot(slot.index, sense: "lf", enabled: !slot.lf.enabled) }
                    }
                    Spacer()
                }
            }
        }
        .padding(.horizontal, 24).padding(.vertical, 14)
        .background(theme.p.panel)
        .task(id: slot.index) { nick = slot.hf.nick }
    }

    private func rename() {
        let name = nick.trimmingCharacters(in: .whitespaces)
        Task { await model.renameSlot(slot.index, sense: "hf", name: name) }
    }
}

/// A slot's emulated tag type as a friendly label (falls back to the enum name with
/// underscores spaced). Kept verbatim across languages - these are product type names.
func slotTypeLabel(_ name: String) -> String {
    if let t = SlotTagType.all.first(where: { $0.name == name }) { return t.label }
    return name.replacingOccurrences(of: "_", with: " ")
}
