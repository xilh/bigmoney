import SwiftUI
import SwiftData

struct HoldingsView: View {
    @Environment(\.modelContext) private var context
    @Query(sort: \AssetLayer.order) private var layers: [AssetLayer]

    @State private var showAddSheet = false
    @State private var editingHolding: Holding?
    @State private var expandedLayers: Set<Int> = [1, 2, 3, 4, 5]

    var totalValue: Double {
        layers.reduce(0) { $0 + $1.totalMarketValue }
    }

    var body: some View {
        NavigationStack {
            List {
                // Total summary
                Section {
                    HStack {
                        Text("总资产")
                            .font(.headline)
                        Spacer()
                        Text(CurrencyFormatter.format(totalValue))
                            .font(.title3)
                            .fontWeight(.bold)
                            .foregroundStyle(.blue)
                    }
                }

                // Layer sections
                ForEach(layers) { layer in
                    layerSection(layer)
                }
            }
            .navigationTitle("持仓管理")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        showAddSheet = true
                    } label: {
                        Image(systemName: "plus")
                    }
                }
            }
            .sheet(isPresented: $showAddSheet) {
                HoldingFormSheet(holding: nil)
            }
            .sheet(item: $editingHolding) { holding in
                HoldingFormSheet(holding: holding)
            }
        }
    }

    @ViewBuilder
    private func layerSection(_ layer: AssetLayer) -> some View {
        Section {
            // Layer header with toggle
            Button {
                withAnimation {
                    if expandedLayers.contains(layer.order) {
                        expandedLayers.remove(layer.order)
                    } else {
                        expandedLayers.insert(layer.order)
                    }
                }
            } label: {
                HStack {
                    Circle()
                        .fill(Color.layerColor(for: layer.order))
                        .frame(width: 10, height: 10)
                    Text(layer.name)
                        .fontWeight(.semibold)
                        .foregroundStyle(.primary)

                    Spacer()

                    VStack(alignment: .trailing) {
                        Text(CurrencyFormatter.format(layer.totalMarketValue))
                            .font(.subheadline)
                            .fontWeight(.medium)
                            .foregroundStyle(.primary)
                        let ratio = totalValue > 0 ? layer.totalMarketValue / totalValue * 100 : 0
                        Text("目标 \(String(format: "%.0f", layer.targetRatio))% · 实际 \(String(format: "%.1f", ratio))%")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }

                    Image(systemName: expandedLayers.contains(layer.order) ? "chevron.up" : "chevron.down")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }

            // Holdings list
            if expandedLayers.contains(layer.order) {
                let sortedHoldings = layer.holdings.sorted { $0.marketValue > $1.marketValue }
                if sortedHoldings.isEmpty {
                    Text("暂无持仓")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .padding(.vertical, 4)
                } else {
                    ForEach(sortedHoldings) { holding in
                        holdingRow(holding)
                    }
                    .onDelete { indexSet in
                        for index in indexSet {
                            context.delete(sortedHoldings[index])
                        }
                        try? context.save()
                    }
                }
            }
        }
    }

    private func holdingRow(_ holding: Holding) -> some View {
        Button {
            editingHolding = holding
        } label: {
            VStack(spacing: 6) {
                HStack {
                    VStack(alignment: .leading, spacing: 2) {
                        HStack(spacing: 4) {
                            Text(holding.name)
                                .font(.subheadline)
                                .fontWeight(.medium)
                                .foregroundStyle(.primary)
                            if !holding.code.isEmpty {
                                Text(holding.code)
                                    .font(.caption2)
                                    .foregroundStyle(.secondary)
                            }
                        }
                        if !holding.platform.isEmpty {
                            Text(holding.platform)
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                        }
                    }

                    Spacer()

                    VStack(alignment: .trailing, spacing: 2) {
                        Text(CurrencyFormatter.format(holding.marketValue))
                            .font(.subheadline)
                            .fontWeight(.medium)
                            .foregroundStyle(.primary)
                        HStack(spacing: 4) {
                            Text(CurrencyFormatter.format(holding.profitLoss, showSign: true))
                                .font(.caption)
                            Text(CurrencyFormatter.formatPercent(holding.profitLossPct))
                                .font(.caption)
                        }
                        .foregroundStyle(holding.profitLoss >= 0 ? .green : .red)
                    }
                }
            }
        }
    }
}

// MARK: - Holding Form Sheet

struct HoldingFormSheet: View {
    @Environment(\.modelContext) private var context
    @Environment(\.dismiss) private var dismiss
    @Query(sort: \AssetLayer.order) private var layers: [AssetLayer]

    let holding: Holding?

    @State private var selectedLayerOrder: Int = 1
    @State private var name = ""
    @State private var code = ""
    @State private var assetType: AssetType = .other
    @State private var quantityText = ""
    @State private var costPriceText = ""
    @State private var currentPriceText = ""
    @State private var marketValueText = ""
    @State private var profitLossText = ""
    @State private var profitLossPctText = ""
    @State private var platform = ""
    @State private var notes = ""

    var isEditing: Bool { holding != nil }

    var body: some View {
        NavigationStack {
            Form {
                Section("基本信息") {
                    Picker("所属层级", selection: $selectedLayerOrder) {
                        ForEach(layers) { layer in
                            Text(layer.name).tag(layer.order)
                        }
                    }

                    TextField("名称", text: $name)

                    TextField("代码（选填）", text: $code)

                    Picker("资产类型", selection: $assetType) {
                        ForEach(AssetType.allCases, id: \.self) { type in
                            Text(type.displayName).tag(type)
                        }
                    }
                }

                Section("数据") {
                    TextField("数量/份额", text: $quantityText)
                        .keyboardType(.decimalPad)
                    TextField("成本价（选填）", text: $costPriceText)
                        .keyboardType(.decimalPad)
                    TextField("当前价（选填）", text: $currentPriceText)
                        .keyboardType(.decimalPad)
                    TextField("市值", text: $marketValueText)
                        .keyboardType(.decimalPad)
                    TextField("盈亏金额", text: $profitLossText)
                        .keyboardType(.decimalPad)
                    TextField("盈亏比例(%)", text: $profitLossPctText)
                        .keyboardType(.decimalPad)
                }

                Section("其他") {
                    TextField("来源平台", text: $platform)
                    TextField("备注", text: $notes, axis: .vertical)
                        .lineLimit(3)
                }
            }
            .navigationTitle(isEditing ? "编辑持仓" : "添加持仓")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("取消") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("保存") { save() }
                        .disabled(name.isEmpty)
                }
            }
            .onAppear { loadExisting() }
        }
    }

    private func loadExisting() {
        guard let h = holding else { return }
        selectedLayerOrder = h.layer?.order ?? 1
        name = h.name
        code = h.code
        assetType = h.assetType
        quantityText = h.quantity > 0 ? String(h.quantity) : ""
        costPriceText = h.costPrice.map { String($0) } ?? ""
        currentPriceText = h.currentPrice.map { String($0) } ?? ""
        marketValueText = h.marketValue > 0 ? String(h.marketValue) : ""
        profitLossText = h.profitLoss != 0 ? String(h.profitLoss) : ""
        profitLossPctText = h.profitLossPct != 0 ? String(h.profitLossPct) : ""
        platform = h.platform
        notes = h.notes
    }

    private func save() {
        guard let layer = layers.first(where: { $0.order == selectedLayerOrder }) else { return }

        let h = holding ?? Holding(layer: layer, name: name)
        h.layer = layer
        h.name = name
        h.code = code
        h.assetType = assetType
        h.quantity = Double(quantityText) ?? 0
        h.costPrice = Double(costPriceText)
        h.currentPrice = Double(currentPriceText)
        h.marketValue = Double(marketValueText) ?? 0
        h.profitLoss = Double(profitLossText) ?? 0
        h.profitLossPct = Double(profitLossPctText) ?? 0
        h.platform = platform
        h.notes = notes

        // Auto-calculate if prices are available
        h.recalculate()

        // If no prices, preserve the market value
        if h.costPrice == nil || h.currentPrice == nil {
            h.marketValue = Double(marketValueText) ?? h.marketValue
            h.profitLoss = Double(profitLossText) ?? h.profitLoss
            h.profitLossPct = Double(profitLossPctText) ?? h.profitLossPct
        }

        if holding == nil {
            context.insert(h)
        }
        try? context.save()
        dismiss()
    }
}

#Preview {
    HoldingsView()
        .modelContainer(for: [AssetLayer.self, Holding.self, Snapshot.self, Transaction.self, Upload.self, ChecklistRecord.self, AppSetting.self], inMemory: true)
}
