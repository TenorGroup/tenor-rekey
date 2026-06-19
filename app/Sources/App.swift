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
        .windowStyle(.titleBar)
        .windowToolbarStyle(.unified(showsTitle: false))
        .commands {
            CommandGroup(replacing: .newItem) {
                Button(l10n.t("open_dump")) { model.openDumpDialog() }
                    .keyboardShortcut("o")
                Button(l10n.t("save_dump")) { model.saveDumpDialog() }
                    .keyboardShortcut("s")
                    .disabled(model.liveDump == nil)
            }
        }
    }
}
