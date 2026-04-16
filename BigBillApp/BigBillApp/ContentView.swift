import SwiftUI

struct ContentView: View {
    @State private var selectedTab: Tab = .dashboard

    enum Tab: String, CaseIterable {
        case dashboard = "仪表盘"
        case holdings = "持仓"
        case upload = "截图识别"
        case rebalance = "再平衡"
        case history = "历史"
        case checklist = "检视清单"
        case settings = "设置"

        var icon: String {
            switch self {
            case .dashboard: return "chart.pie.fill"
            case .holdings: return "briefcase.fill"
            case .upload: return "camera.fill"
            case .rebalance: return "scale.3d"
            case .history: return "chart.line.uptrend.xyaxis"
            case .checklist: return "checklist"
            case .settings: return "gearshape.fill"
            }
        }
    }

    var body: some View {
        TabView(selection: $selectedTab) {
            DashboardView()
                .tabItem {
                    Label(Tab.dashboard.rawValue, systemImage: Tab.dashboard.icon)
                }
                .tag(Tab.dashboard)

            HoldingsView()
                .tabItem {
                    Label(Tab.holdings.rawValue, systemImage: Tab.holdings.icon)
                }
                .tag(Tab.holdings)

            UploadView()
                .tabItem {
                    Label(Tab.upload.rawValue, systemImage: Tab.upload.icon)
                }
                .tag(Tab.upload)

            RebalanceView()
                .tabItem {
                    Label(Tab.rebalance.rawValue, systemImage: Tab.rebalance.icon)
                }
                .tag(Tab.rebalance)

            HistoryView()
                .tabItem {
                    Label(Tab.history.rawValue, systemImage: Tab.history.icon)
                }
                .tag(Tab.history)

            SettingsView()
                .tabItem {
                    Label(Tab.settings.rawValue, systemImage: Tab.settings.icon)
                }
                .tag(Tab.settings)
        }
        .tint(Color(hex: "3B82F6"))
    }
}

#Preview {
    ContentView()
        .modelContainer(for: [
            AssetLayer.self, Holding.self, Snapshot.self,
            Transaction.self, Upload.self, ChecklistRecord.self, AppSetting.self,
        ], inMemory: true)
}
