import SwiftUI
import SwiftData

@main
struct BigBillApp: App {
    var body: some Scene {
        WindowGroup {
            ContentView()
        }
        .modelContainer(for: [
            AssetLayer.self,
            Holding.self,
            Snapshot.self,
            Transaction.self,
            Upload.self,
            ChecklistRecord.self,
            AppSetting.self,
        ])
    }
}
