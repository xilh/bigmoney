import SwiftUI
import SwiftData
import Charts

struct RebalanceView: View {
    @Environment(\.modelContext) private var context
    @Query(sort: \AssetLayer.order) private var layers: [AssetLayer]

    @State private var showAllocateSheet = false
    @State private var showProtocols = false

    private var totalValue: Double {
        layers.reduce(0) { $0 + $1.totalMarketValue }
    }

    private var layersData: [LayerData] {
        layers.map { LayerData(id: $0.order, name: $0.name, targetRatio: $0.targetRatio, actualValue: $0.totalMarketValue) }
    }

    private var rebalanceResult: RebalanceResult {
        RebalanceService.calculate(layersData: layersData, totalValue: totalValue)
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 16) {
                    // Alerts
                    alertsSection

                    // Chart comparison
                    comparisonChart

                    // Layer details
                    layerDetailsSection

                    // Suggestions
                    suggestionsSection

                    // Drawdown protocols
                    protocolsSection
                }
                .padding()
            }
            .background(Color(.systemGroupedBackground))
            .navigationTitle("再平衡")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("资金分配") {
                        showAllocateSheet = true
                    }
                }
            }
            .sheet(isPresented: $showAllocateSheet) {
                AllocateFundsSheet(layersData: layersData, totalValue: totalValue)
            }
        }
    }

    // MARK: - Alerts

    private var alertsSection: some View {
        VStack(spacing: 8) {
            ForEach(rebalanceResult.alerts) { alert in
                HStack(spacing: 8) {
                    Image(systemName: alert.level == "critical" ? "exclamationmark.triangle.fill" : "exclamationmark.circle.fill")
                        .foregroundStyle(alert.level == "critical" ? .red : .orange)

                    VStack(alignment: .leading, spacing: 2) {
                        Text(alert.message)
                            .font(.caption)
                        Text(alert.action)
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }

                    Spacer()
                }
                .padding(10)
                .background(
                    (alert.level == "critical" ? Color.red : Color.orange).opacity(0.1)
                )
                .clipShape(RoundedRectangle(cornerRadius: 8))
            }

            if rebalanceResult.alerts.isEmpty && totalValue > 0 {
                HStack {
                    Image(systemName: "checkmark.circle.fill")
                        .foregroundStyle(.green)
                    Text("所有层级均在合理范围内")
                        .font(.subheadline)
                }
                .padding()
                .frame(maxWidth: .infinity)
                .background(Color.green.opacity(0.1))
                .clipShape(RoundedRectangle(cornerRadius: 8))
            }
        }
    }

    // MARK: - Comparison Chart

    private var comparisonChart: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("目标 vs 实际")
                .font(.headline)

            if totalValue > 0 {
                Chart {
                    ForEach(rebalanceResult.layers, id: \.id) { layer in
                        BarMark(
                            x: .value("层级", layer.name),
                            y: .value("比例", layer.targetRatio)
                        )
                        .foregroundStyle(.blue.opacity(0.4))
                        .position(by: .value("Type", "目标"))

                        BarMark(
                            x: .value("层级", layer.name),
                            y: .value("比例", layer.actualRatio)
                        )
                        .foregroundStyle(.blue)
                        .position(by: .value("Type", "实际"))
                    }
                }
                .frame(height: 200)
                .chartLegend(.hidden)

                HStack(spacing: 16) {
                    HStack(spacing: 4) {
                        Rectangle().fill(.blue.opacity(0.4)).frame(width: 12, height: 12)
                        Text("目标").font(.caption)
                    }
                    HStack(spacing: 4) {
                        Rectangle().fill(.blue).frame(width: 12, height: 12)
                        Text("实际").font(.caption)
                    }
                }
            } else {
                Text("暂无数据").foregroundStyle(.secondary)
            }
        }
        .padding()
        .background(.regularMaterial)
        .clipShape(RoundedRectangle(cornerRadius: 12))
    }

    // MARK: - Layer Details

    private var layerDetailsSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("各层级详情")
                .font(.headline)

            ForEach(rebalanceResult.layers, id: \.id) { layer in
                VStack(spacing: 6) {
                    HStack {
                        Circle()
                            .fill(Color.layerColor(for: layer.id))
                            .frame(width: 8, height: 8)
                        Text(layer.name)
                            .font(.subheadline)
                            .fontWeight(.medium)
                        Spacer()
                        Text(layer.status == "critical" ? "🚨" : layer.status == "warning" ? "⚠️" : "✅")
                    }

                    HStack {
                        VStack(alignment: .leading) {
                            Text("目标: \(String(format: "%.1f", layer.targetRatio))%")
                            Text("实际: \(String(format: "%.1f", layer.actualRatio))%")
                        }
                        .font(.caption)
                        .foregroundStyle(.secondary)

                        Spacer()

                        VStack(alignment: .trailing) {
                            Text("偏差: \(String(format: "%+.1f", layer.deviation))%")
                                .foregroundStyle(
                                    abs(layer.deviation) > 5 ? .red :
                                    abs(layer.deviation) > 3 ? .orange : .green
                                )
                            if layer.adjustment != 0 {
                                Text("\(layer.adjustment > 0 ? "减持" : "增持") \(CurrencyFormatter.format(abs(layer.adjustment)))")
                            }
                        }
                        .font(.caption)
                    }
                }
                .padding(10)
                .background(Color(.secondarySystemGroupedBackground))
                .clipShape(RoundedRectangle(cornerRadius: 8))
            }
        }
        .padding()
        .background(.regularMaterial)
        .clipShape(RoundedRectangle(cornerRadius: 12))
    }

    // MARK: - Suggestions

    private var suggestionsSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("调整建议")
                .font(.headline)

            if rebalanceResult.suggestions.isEmpty {
                Text("暂无调整建议")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            } else {
                ForEach(rebalanceResult.suggestions) { suggestion in
                    HStack {
                        Image(systemName: suggestion.type == "sell" ? "arrow.down.circle" : "arrow.up.circle")
                            .foregroundStyle(suggestion.type == "sell" ? .red : .green)
                        Text(suggestion.message)
                            .font(.subheadline)
                        Spacer()
                    }
                    .padding(8)
                    .background(
                        (suggestion.type == "sell" ? Color.red : Color.green).opacity(0.08)
                    )
                    .clipShape(RoundedRectangle(cornerRadius: 8))
                }
            }
        }
        .padding()
        .background(.regularMaterial)
        .clipShape(RoundedRectangle(cornerRadius: 12))
    }

    // MARK: - Drawdown Protocols

    private var protocolsSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            Button {
                withAnimation { showProtocols.toggle() }
            } label: {
                HStack {
                    Text("下跌应对协议")
                        .font(.headline)
                        .foregroundStyle(.primary)
                    Spacer()
                    Image(systemName: showProtocols ? "chevron.up" : "chevron.down")
                        .foregroundStyle(.secondary)
                }
            }

            if showProtocols {
                ForEach(RebalanceService.drawdownProtocols, id: \.name) { protocol_ in
                    VStack(alignment: .leading, spacing: 8) {
                        Text(protocol_.name)
                            .font(.subheadline)
                            .fontWeight(.semibold)

                        ForEach(protocol_.rules, id: \.threshold) { rule in
                            VStack(alignment: .leading, spacing: 2) {
                                Text(rule.threshold)
                                    .font(.caption)
                                    .fontWeight(.medium)
                                    .foregroundStyle(.orange)
                                Text(rule.action)
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                            .padding(8)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .background(Color(.tertiarySystemGroupedBackground))
                            .clipShape(RoundedRectangle(cornerRadius: 6))
                        }
                    }
                    .padding(.vertical, 4)
                }
            }
        }
        .padding()
        .background(.regularMaterial)
        .clipShape(RoundedRectangle(cornerRadius: 12))
    }
}

// MARK: - Allocate Funds Sheet

struct AllocateFundsSheet: View {
    @Environment(\.dismiss) private var dismiss
    let layersData: [LayerData]
    let totalValue: Double

    @State private var amountText = ""
    @State private var allocations: [AllocationSuggestion] = []

    var body: some View {
        NavigationStack {
            VStack(spacing: 16) {
                TextField("新增资金金额 (元)", text: $amountText)
                    .keyboardType(.decimalPad)
                    .textFieldStyle(.roundedBorder)
                    .padding(.horizontal)

                Button("计算分配") {
                    let amount = Double(amountText) ?? 0
                    allocations = RebalanceService.allocateNewFunds(
                        layersData: layersData, totalValue: totalValue, newAmount: amount
                    )
                }
                .buttonStyle(.borderedProminent)
                .disabled(amountText.isEmpty)

                if !allocations.isEmpty {
                    List(allocations) { alloc in
                        HStack {
                            VStack(alignment: .leading) {
                                Text(alloc.layer)
                                    .font(.subheadline)
                                    .fontWeight(.medium)
                                Text(alloc.reason)
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                            Spacer()
                            Text(CurrencyFormatter.format(alloc.amount))
                                .font(.subheadline)
                                .fontWeight(.bold)
                                .foregroundStyle(.blue)
                        }
                    }
                }

                Spacer()
            }
            .padding(.top)
            .navigationTitle("新资金分配")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("关闭") { dismiss() }
                }
            }
        }
    }
}

#Preview {
    RebalanceView()
        .modelContainer(for: [AssetLayer.self, Holding.self, Snapshot.self, Transaction.self, Upload.self, ChecklistRecord.self, AppSetting.self], inMemory: true)
}
