import SwiftUI
import SwiftData
import Charts

struct DashboardView: View {
    @Environment(\.modelContext) private var context
    @Query(sort: \AssetLayer.order) private var layers: [AssetLayer]
    @Query(sort: \Transaction.date, order: .reverse) private var transactions: [Transaction]

    @State private var showTransactionSheet = false

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 16) {
                    // Summary cards
                    summaryCards

                    // Allocation chart
                    allocationChart

                    // Platform distribution
                    platformChart

                    // Deviation analysis
                    deviationSection

                    // Recent transactions
                    recentTransactions
                }
                .padding()
            }
            .background(Color(.systemGroupedBackground))
            .navigationTitle("仪表盘")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        createSnapshot()
                    } label: {
                        Image(systemName: "camera.circle")
                    }
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        showTransactionSheet = true
                    } label: {
                        Image(systemName: "plus.circle")
                    }
                }
            }
            .sheet(isPresented: $showTransactionSheet) {
                TransactionFormSheet()
            }
            .onAppear {
                DataService.seedDefaultLayers(context: context)
            }
        }
    }

    private var totalValue: Double {
        layers.reduce(0) { $0 + $1.totalMarketValue }
    }

    private var totalProfit: Double {
        layers.reduce(0) { $0 + $1.totalProfitLoss }
    }

    private var totalCost: Double {
        layers.flatMap(\.holdings).reduce(0.0) { sum, h in
            if let cost = h.costPrice, h.quantity > 0 {
                return sum + cost * h.quantity
            }
            return sum
        }
    }

    private var totalProfitPct: Double {
        totalCost > 0 ? totalProfit / totalCost * 100 : 0
    }

    private var criticalAlerts: Int {
        layers.filter { layer in
            let ratio = totalValue > 0 ? layer.totalMarketValue / totalValue * 100 : 0
            return abs(ratio - layer.targetRatio) > 5
        }.count
    }

    // MARK: - Summary Cards

    private var summaryCards: some View {
        LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 12) {
            StatCard(title: "总资产", value: CurrencyFormatter.format(totalValue), color: .blue)
            StatCard(
                title: "总盈亏",
                value: CurrencyFormatter.format(totalProfit, showSign: true),
                subtitle: CurrencyFormatter.formatPercent(totalProfitPct),
                color: totalProfit >= 0 ? .green : .red
            )
            StatCard(title: "持仓数", value: "\(layers.flatMap(\.holdings).count)", color: .purple)
            StatCard(
                title: "偏差警告",
                value: "\(criticalAlerts)",
                color: criticalAlerts > 0 ? .red : .green
            )
        }
    }

    // MARK: - Allocation Chart

    private var allocationChart: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("资产配置")
                .font(.headline)

            if totalValue > 0 {
                Chart {
                    ForEach(layers) { layer in
                        let ratio = layer.totalMarketValue / totalValue * 100
                        SectorMark(
                            angle: .value(layer.name, ratio),
                            innerRadius: .ratio(0.5),
                            angularInset: 1
                        )
                        .foregroundStyle(Color.layerColor(for: layer.order))
                        .annotation(position: .overlay) {
                            if ratio > 5 {
                                Text("\(String(format: "%.0f", ratio))%")
                                    .font(.caption2).bold()
                                    .foregroundStyle(.white)
                            }
                        }
                    }
                }
                .frame(height: 200)

                // Legend
                ForEach(layers) { layer in
                    let ratio = totalValue > 0 ? layer.totalMarketValue / totalValue * 100 : 0
                    HStack {
                        Circle()
                            .fill(Color.layerColor(for: layer.order))
                            .frame(width: 10, height: 10)
                        Text(layer.name)
                            .font(.caption)
                        Spacer()
                        Text(CurrencyFormatter.format(layer.totalMarketValue))
                            .font(.caption)
                            .foregroundStyle(.secondary)
                        Text("(\(String(format: "%.1f", ratio))%)")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                }
            } else {
                Text("暂无数据")
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, minHeight: 100)
            }
        }
        .padding()
        .background(.regularMaterial)
        .clipShape(RoundedRectangle(cornerRadius: 12))
    }

    // MARK: - Platform Distribution

    private var platformChart: some View {
        let platforms = Dictionary(grouping: layers.flatMap(\.holdings)) { $0.platform.isEmpty ? "其他/未知" : $0.platform }
            .map { (name: $0.key, value: $0.value.reduce(0.0) { $0 + $1.marketValue }) }
            .sorted { $0.value > $1.value }

        return VStack(alignment: .leading, spacing: 12) {
            Text("平台分布")
                .font(.headline)

            if !platforms.isEmpty {
                Chart(platforms, id: \.name) { platform in
                    SectorMark(
                        angle: .value(platform.name, platform.value),
                        innerRadius: .ratio(0.4),
                        angularInset: 1
                    )
                    .annotation(position: .overlay) {
                        if platform.value / totalValue > 0.08 {
                            Text(platform.name)
                                .font(.caption2)
                                .foregroundStyle(.white)
                        }
                    }
                }
                .frame(height: 180)

                ForEach(platforms, id: \.name) { p in
                    HStack {
                        Text(p.name).font(.caption)
                        Spacer()
                        Text(CurrencyFormatter.format(p.value)).font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
        }
        .padding()
        .background(.regularMaterial)
        .clipShape(RoundedRectangle(cornerRadius: 12))
    }

    // MARK: - Deviation

    private var deviationSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("偏差分析")
                .font(.headline)

            ForEach(layers) { layer in
                let actualRatio = totalValue > 0 ? layer.totalMarketValue / totalValue * 100 : 0
                let deviation = actualRatio - layer.targetRatio

                HStack {
                    Circle()
                        .fill(Color.layerColor(for: layer.order))
                        .frame(width: 8, height: 8)
                    Text(layer.name)
                        .font(.caption)
                        .lineLimit(1)

                    Spacer()

                    Text("\(String(format: "%.1f", layer.targetRatio))%")
                        .font(.caption2)
                        .foregroundStyle(.secondary)

                    Text("→")
                        .font(.caption2)
                        .foregroundStyle(.secondary)

                    Text("\(String(format: "%.1f", actualRatio))%")
                        .font(.caption2)
                        .fontWeight(.medium)

                    Text("(\(String(format: "%+.1f", deviation))%)")
                        .font(.caption2)
                        .foregroundStyle(
                            abs(deviation) > 5 ? .red :
                            abs(deviation) > 3 ? .orange : .green
                        )
                }
            }
        }
        .padding()
        .background(.regularMaterial)
        .clipShape(RoundedRectangle(cornerRadius: 12))
    }

    // MARK: - Recent Transactions

    private var recentTransactions: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("最近交易")
                .font(.headline)

            if transactions.prefix(5).isEmpty {
                Text("暂无交易记录")
                    .foregroundStyle(.secondary)
                    .font(.caption)
            } else {
                ForEach(Array(transactions.prefix(5))) { tx in
                    HStack {
                        Text(tx.action.displayName)
                            .font(.caption)
                            .padding(.horizontal, 6)
                            .padding(.vertical, 2)
                            .background(tx.action == .transfer ? Color.green.opacity(0.15) : Color.red.opacity(0.15))
                            .clipShape(Capsule())

                        Text(tx.assetName)
                            .font(.subheadline)
                            .lineLimit(1)

                        Spacer()

                        Text(CurrencyFormatter.format(tx.amount))
                            .font(.subheadline)
                            .fontWeight(.medium)
                    }
                }
            }
        }
        .padding()
        .background(.regularMaterial)
        .clipShape(RoundedRectangle(cornerRadius: 12))
    }

    // MARK: - Actions

    private func createSnapshot() {
        let layerValues = Dictionary(uniqueKeysWithValues: layers.map { ($0.name, $0.totalMarketValue) })
        let layerRatios = Dictionary(uniqueKeysWithValues: layers.map { ($0.name, totalValue > 0 ? $0.totalMarketValue / totalValue * 100 : 0) })

        let snapshot = Snapshot(
            date: Date(),
            totalValue: totalValue,
            layerValues: layerValues,
            layerRatios: layerRatios
        )
        context.insert(snapshot)
        try? context.save()
    }
}

// MARK: - Stat Card

struct StatCard: View {
    let title: String
    let value: String
    var subtitle: String? = nil
    let color: Color

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title)
                .font(.caption)
                .foregroundStyle(.secondary)
            Text(value)
                .font(.title3)
                .fontWeight(.bold)
                .foregroundStyle(color)
                .lineLimit(1)
                .minimumScaleFactor(0.7)
            if let subtitle {
                Text(subtitle)
                    .font(.caption2)
                    .foregroundStyle(color.opacity(0.8))
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding()
        .background(.regularMaterial)
        .clipShape(RoundedRectangle(cornerRadius: 12))
    }
}

// MARK: - Transaction Form Sheet

struct TransactionFormSheet: View {
    @Environment(\.modelContext) private var context
    @Environment(\.dismiss) private var dismiss

    @State private var action: TransactionAction = .transfer
    @State private var assetName = ""
    @State private var amountText = ""
    @State private var date = Date()

    var body: some View {
        NavigationStack {
            Form {
                Picker("操作类型", selection: $action) {
                    ForEach(TransactionAction.allCases, id: \.self) { a in
                        Text(a.displayName).tag(a)
                    }
                }

                TextField("资产名称", text: $assetName)
                TextField("金额", text: $amountText)
                    .keyboardType(.decimalPad)
                DatePicker("日期", selection: $date, displayedComponents: .date)
            }
            .navigationTitle("添加交易记录")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("取消") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("保存") {
                        let amount = Double(amountText) ?? 0
                        guard !assetName.isEmpty, amount > 0 else { return }
                        let tx = Transaction(action: action, assetName: assetName, amount: amount, date: date)
                        context.insert(tx)
                        try? context.save()
                        dismiss()
                    }
                }
            }
        }
    }
}

#Preview {
    DashboardView()
        .modelContainer(for: [AssetLayer.self, Holding.self, Snapshot.self, Transaction.self, Upload.self, ChecklistRecord.self, AppSetting.self], inMemory: true)
}
