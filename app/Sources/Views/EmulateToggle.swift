import SwiftUI

/// Reader <-> emulate mode toggle for the action bar, shown only when the connected
/// device can emulate (capabilities.emulate; a plain reader like the X7 never sees it).
/// In emulate mode the device presents its active slot as a tag; the status monitor
/// pauses so a poll cannot flip it back (see AppModel.toggleEmulate). Reuses the
/// action-bar button styling so it reads as one of the labelled verbs.
struct EmulateToggle: View {
    @Environment(AppModel.self) private var model
    @Environment(L10n.self) private var l

    var body: some View {
        ActionButton(
            title: l.t(model.emulating ? "emulate_mode" : "reader_mode"),
            icon: model.emulating ? "wave.3.right" : "dot.radiowaves.left.and.right",
            on: model.emulating,
            enabled: model.readerOnline && !model.decoding && !model.cloning
                && !model.formatting && !model.slotBusy,
            help: l.t("emulate_hint")
        ) {
            Task { await model.toggleEmulate() }
        }
    }
}
