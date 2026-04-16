import SwiftUI
import SwiftData
import Charts

struct HistoryView: View {
    @Environment(\.modelContext) private var context
    @Query(sort: \Snapshot.date, order: .reverse) private var snapshots: [Snapshot]
    @Query(sort: \Transaction.date, order: .reverse) private var transactions: [Transaction]

    @State private var selectedTab = 0
    @State private var showPerformance = false
    @State private var startDate = Calendar.current.date(byAdding: .month, value: -3, to: Date()) ?? Date()
    @State private var endDate = Date()
    @State private var performanceResult: PerformanceResult?

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                // Tab picker
                Picker("", selection: $selectedTab) {
                    Text("快照").tag(0)
                    Text("交易记录").tag(1)
                    Text("收益分析").tag(2)
                }
                .pickerStyle(.segmented)
                .padding()

                ScrollView {
                    switch selectedTab {
                    case 0: snapshotSection
                    case 1: transactionSection
                    case 2: performanceSection
                    default: EmptyView()
                    }
                }
            }
            .background(Color(.systemGroupedBackground))
            .navigationTitle("历史记录")
        }
    }

    // MARK: - Snapshots

    private var snapshotSection: some View {
        VStack(spacing: 16) {
            // Chart
            if snapshots.count >= 2 {
                snapshotChart
            }

            // List
            VStack(alignment: .leading, spacing: 8) {
                Text("快照记录")
                    .font(.headline)
                    .padding(.horizontal)

                if snapshots.isEmpty {
                    Text("暂无快照记录，请在仪表盘中创建快照")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .padding()
                } else {
                    ForEach(snapshots) { snapshot in
                        snapshotRow(snapshot)
                    }
                }
            }
        }
        .padding()
    }

    private var snapshotChart: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("资产趋势")
                .font(.headline)

            let sorted = snapshots.sorted { $0.date < $1.date }
            Chart(sorted) { snapshot in
                LineMark(
                    x: .value("日期", snapshot.date),
                    y: .value("总资产", snapshot.totalValue)
                )
                .foregroundStyle(.blue)

                AreaMark(
                    x: .value("日期", snapshot.date),
                    y: .value("总资产", snapshot.totalValue)
                )
                .foregroundStyle(.blue.opacity(0.1))
            }
            .frame(height: 200)
            .chartYAxis {
                AxisMarks(position: .leading) { value in
                    AxisValueLabel {
                        if let v = value.as(Double.self) {
                            Text(CurrencyFormatter.format(v))
                                .font(.caption2)
                        }
                    }
                }
            }
        }
        .padding()
        .background(.regularMaterial)
        .clipShape(RoundedRectangle(cornerRadius: 12))
    }

    private func snapshotRow(_ snapshot: Snapshot) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                VStack(alignment: .leading) {
                    Text(snapshot.date.fullString)
                        .font(.subheadline)
                        .fontWeight(.medium)
                    if !snapshot.notes.isEmpty {
                        Text(snapshot.notes)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
                Spacer()
                Text(CurrencyFormatter.format(snapshot.totalValue))
                    .font(.subheadline)
                    .fontWeight(.bold)
                    .foregroundStyle(.blue)
            }

            // Layer breakdown
            let values = snapshot.layerValues
            if !values.isEmpty {
                HStack(spacing: 4) {
                    ForEach(Array(values.sorted(by: { $0.key < $1.key })), id: \.key) { name, value in
                        VStack {
                            Text(String(name.prefix(4)))
                                .font(.system(size: 8))
                                .foregroundStyle(.secondary)
                            Text(CurrencyFormatter.format(value))
                                .font(.system(size: 9))
                        }
                        .frame(maxWidth: .infinity)
                    }
                }
            }
        }
        .padding(10)
        .background(Color(.secondarySystemGroupedBackground))
        .clipShape(RoundedRectangle(cornerRadius: 8))
        .swipeActions(edge: .trailing) {
            Button(role: .destructive) {
                context.delete(snapshot)
                try? context.save()
            } label: {
                Label("删除", systemImage: "trash")
            }
        }
    }

    // MARK: - Transactions

    private var transactionSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("交易记录")
                .font(.headline)
                .padding(.horizontal)

            if transactions.isEmpty {
                Text("暂无交易记录")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .padding()
            } else {
                ForEach(transactions) { tx in
                    HStack {
                        Text(tx.action.displayName)
                            .font(.caption)
                            .padding(.horizontal, 6)
                            .padding(.vertical, 2)
                            .background(
                                (tx.action == .buy || tx.action == .transfer) ?
                                Color.green.opacity(0.15) : Color.red.opacity(0.15)
                            )
                            .clipShape(Capsule())

                        VStack(alignment: .leading) {
                            Text(tx.assetName)
                                .font(.subheadline)
                            Text(tx.date.shortString)
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                        }

                        Spacer()

                        Text(CurrencyFormatter.format(tx.amount))
                            .font(.subheadline)
                            .fontWeight(.medium)
                    }
                    .padding(.horizontal)
                    .padding(.vertical, 4)
                }
            }
        }
        .padding(.vertical)
    }

    // MARK: - Performance

    private var performanceSection: some View {
        VStack(spacing: 16) {
            VStack(alignment: .leading, spacing: 12) {
                Text("区间收益计算")
                    .font(.headline)

                DatePicker("开始日期", selection: $startDate, displayedComponents: .date)
                DatePicker("结束日期", selection: $endDate, displayedComponents: .date)

                Button("计算收益") {
                    performanceResult = PerformanceService.calculateIntervalPerformance(
                        startDate: startDate, endDate: endDate, context: context
                    )
                }
                .buttonStyle(.borderedProminent)
            }
            .padding()
            .background(.regularMaterial)
            .clipShape(RoundedRectangle(cornerRadius: 12))

            if let result = performanceResult {
                VStack(alignment: .leading, spacing: 12) {
                    Text("收益结果 (Modified Dietz)")
                        .font(.headline)

                    LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 12) {
                        resultCard("期初资产", CurrencyFormatter.format(result.startValue))
                        resultCard("期末资产", CurrencyFormatter.format(result.endValue))
                        resultCard("净资金流入", CurrencyFormatter.format(result.netCashflow, showSign: true))
                        resultCard("绝对收益", CurrencyFormatter.format(result.absoluteProfit, showSign: true))
                    }

                    // Return rate highlight
                    HStack {
                        Text("区间回报率")
                            .font(.subheadline)
                        Spacer()
                        Text(CurrencyFormatter.formatPercent(result.returnRatePct))
                            .font(.title2)
                            .fontWeight(.bold)
                            .foregroundStyle(result.returnRatePct >= 0 ? .green : .red)
                    }
                    .padding()
                    .background(
                        (result.returnRatePct >= 0 ? Color.green : Color.red).opacity(0.1)
                    )
                    .clipShape(RoundedRectangle(cornerRadius: 8))
                }
                .padding()
                .background(.regularMaterial)
                .clipShape(RoundedRectangle(cornerRadius: 12))
            }
        }
        .padding()
    }

    private func resultCard(_ title: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title)
                .font(.caption)
                .foregroundStyle(.secondary)
            Text(value)
                .font(.subheadline)
                .fontWeight(.medium)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(10)
        .background(Color(.secondarySystemGroupedBackground))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

#Preview {
    HistoryView()
        .modelContainer(for: [AssetLayer.self, Holding.self, Snapshot.self, Transaction.self, Upload.self, ChecklistRecord.self, AppSetting.self], inMemory: true)
}
