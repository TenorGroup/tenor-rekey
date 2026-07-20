import SwiftUI
import AppKit

/// App delegate carrying the quit guard: while a firmware flash is writing, quitting must
/// not tear the app (and the flasher subprocess) down mid-write, which can brick the
/// device. The shell installs `terminationGuard`; it warns and cancels the quit while a
/// flash is in progress, and otherwise allows it.
final class AppDelegate: NSObject, NSApplicationDelegate {
    static var terminationGuard: @MainActor () -> NSApplication.TerminateReply = { .terminateNow }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        MainActor.assumeIsolated { AppDelegate.terminationGuard() }
    }
}

@main
struct TenorRekeyApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @State private var model = AppModel()
    @State private var theme = Theme()
    @State private var l10n = L10n()

    init() { Typeface.registerBundledFonts() }

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
                    .disabled(model.source == nil)
            }
            CommandMenu(l10n.t("card")) {
                Button(l10n.t("decode")) { Task { await model.decode() } }
                    .keyboardShortcut("r")
                    .disabled(!model.readerOnline || model.decoding || model.emulating)
                Button(l10n.t("clone")) { model.cloneSheet = true }
                    .disabled(model.cloneSource == nil || model.cloning || model.decoding || model.formatting || model.emulating)
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
