import SwiftUI
import UniformTypeIdentifiers

/// The Settings scene (⌘,). Native Form/List here (Codex r5: Form only at
/// settings) - the instrument canvas styling stays in the main window.
struct SettingsView: View {
    @Environment(L10n.self) private var l

    var body: some View {
        TabView {
            DeviceSettings().tabItem { Label(l.t("device"), systemImage: "cpu") }
            DictionarySettings().tabItem { Label(l.t("dictionaries"), systemImage: "key") }
            GeneralSettings().tabItem { Label(l.t("general"), systemImage: "gearshape") }
        }
        .frame(width: 470, height: 400)
    }
}

private struct DeviceSettings: View {
    @Environment(AppModel.self) private var model
    @Environment(L10n.self) private var l
    var body: some View {
        Form {
            Section {
                spec(l.t("model"), model.info?.model)
                spec(l.t("serial"), model.info?.serial)
                spec("mcu", model.info?.hw)
                LabeledContent(l.t("status")) {
                    Text(model.readerOnline ? l.t("reader_online") : l.t("reader_offline"))
                }
            }
            Button(l.t("reconnect")) { Task { await model.connect() } }
        }
        .formStyle(.grouped)
    }
    private func spec(_ label: String, _ value: String?) -> some View {
        LabeledContent(label) {
            Text(value ?? "-").font(.system(.body, design: .monospaced)).textSelection(.enabled)
        }
    }
}

private struct DictionarySettings: View {
    @Environment(AppModel.self) private var model
    @Environment(L10n.self) private var l
    @State private var newKey = ""
    @State private var selection = Set<String>()

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                TextField(l.t("key_hint"), text: $newKey)
                    .font(.system(.body, design: .monospaced))
                    .onSubmit(addKey)
                Button(l.t("add"), action: addKey)
                    .disabled(KeyStore.normalized(newKey) == nil)
                Button(l.t("import")) { importDialog() }
            }
            .padding(12)
            List(selection: $selection) {
                ForEach(model.keyStore.keys, id: \.self) { key in
                    Text(key).font(.system(.body, design: .monospaced))
                }
                .onDelete { model.keyStore.remove(at: $0) }
                .onMove { model.keyStore.move(from: $0, to: $1) }
            }
            HStack {
                Text("\(model.keyStore.keys.count) \(l.t("user_keys"))  ·  +\(model.builtinKeyCount) \(l.t("builtin_keys"))")
                    .font(.caption).foregroundStyle(.secondary)
                Spacer()
                Button(l.t("remove")) { removeSelected() }.disabled(selection.isEmpty)
            }
            .padding(12)
        }
    }

    private func addKey() {
        if model.keyStore.add(newKey) { newKey = "" }
    }
    private func removeSelected() {
        let idx = IndexSet(model.keyStore.keys.enumerated().filter { selection.contains($0.element) }.map(\.offset))
        model.keyStore.remove(at: idx)
        selection.removeAll()
    }
    private func importDialog() {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.plainText, .text, .data]
        panel.allowsOtherFileTypes = true
        if panel.runModal() == .OK, let url = panel.url, let text = try? String(contentsOf: url, encoding: .utf8) {
            model.keyStore.importText(text)
        }
    }
}

private struct GeneralSettings: View {
    @Environment(Theme.self) private var theme
    @Environment(L10n.self) private var l
    @State private var exportFolder = UserDefaults.standard.string(forKey: "rekey.exportFolder") ?? ""

    var body: some View {
        @Bindable var theme = theme
        @Bindable var l = l
        Form {
            Picker(l.t("appearance"), selection: $theme.appearance) {
                Text(l.t("lang_system")).tag(Appearance.system)
                Text(l.t("light")).tag(Appearance.light)
                Text(l.t("dark")).tag(Appearance.dark)
            }
            Picker(l.t("language"), selection: $l.lang) {
                ForEach(AppLang.allCases) { lang in
                    Text(lang == .system ? l.systemDisplay() : lang.display).tag(lang)
                }
            }
            LabeledContent(l.t("export_folder")) {
                HStack {
                    Text(exportFolder.isEmpty ? l.t("export_default") : (exportFolder as NSString).lastPathComponent)
                        .foregroundStyle(.secondary).lineLimit(1)
                    Button(l.t("choose")) { chooseFolder() }
                }
            }
        }
        .formStyle(.grouped)
    }

    private func chooseFolder() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        if panel.runModal() == .OK, let url = panel.url {
            exportFolder = url.path
            UserDefaults.standard.set(url.path, forKey: "rekey.exportFolder")
        }
    }
}
