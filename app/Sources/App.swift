import SwiftUI

@main
struct TenorRekeyApp: App {
    @State private var model = AppModel()
    @State private var theme = Theme()
    @State private var l10n = L10n()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environment(model)
                .environment(theme)
                .environment(l10n)
                .frame(minWidth: 940, minHeight: 600)
        }
        .windowStyle(.hiddenTitleBar)
        .commands {
            CommandGroup(replacing: .newItem) {
                Button(l10n.t("open_dump")) { model.openDumpDialog() }
                    .keyboardShortcut("o")
                Button(l10n.t("save_dump")) { model.saveDumpDialog() }
                    .keyboardShortcut("s")
                    .disabled(model.liveDump == nil)
            }
            CommandMenu(l10n.t("card")) {
                Button(l10n.t("decode")) { Task { await model.decode() } }
                    .keyboardShortcut("r")
                    .disabled(model.card == nil || model.decoding)
                Button(l10n.t("clone")) { model.cloneSheet = true }
                    .disabled(model.source == nil || model.card == nil || model.cloning)
                Divider()
                Button(l10n.t("apdu")) { model.apduOpen.toggle() }
                    .keyboardShortcut("t")
            }
            CommandGroup(after: .sidebar) {
                Button(l10n.t("inspector")) { model.inspectorOpen.toggle() }
                    .keyboardShortcut("i", modifiers: [.command, .option])
                Button(l10n.t("light_dark")) { theme.toggle() }
                    .keyboardShortcut("l", modifiers: [.command, .shift])
            }
        }

        Settings {
            SettingsView()
                .environment(model)
                .environment(theme)
                .environment(l10n)
        }
    }
}
