import SwiftUI

@main
struct TenorCardApp: App {
    var body: some Scene {
        WindowGroup {
            RootView()
                .frame(minWidth: 940, minHeight: 600)
        }
        .windowStyle(.titleBar)
        .windowToolbarStyle(.unified(showsTitle: false))
    }
}
