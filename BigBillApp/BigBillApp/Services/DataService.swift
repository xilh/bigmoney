import Foundation
import SwiftData

/// Handles seeding default layers and data export/import
enum DataService {
    static func seedDefaultLayers(context: ModelContext) {
        let descriptor = FetchDescriptor<AssetLayer>()
        let existing = (try? context.fetch(descriptor)) ?? []
        guard existing.isEmpty else { return }

        for layer in AssetLayer.seedLayers() {
            context.insert(layer)
        }
        try? context.save()
    }

    static func getLayersData(context: ModelContext) -> (layers: [LayerData], totalValue: Double) {
        let descriptor = FetchDescriptor<AssetLayer>(sortBy: [SortDescriptor(\.order)])
        let layers = (try? context.fetch(descriptor)) ?? []
        var totalValue: Double = 0
        var layersData: [LayerData] = []

        for layer in layers {
            let value = layer.totalMarketValue
            totalValue += value
            layersData.append(LayerData(
                id: layer.order,
                name: layer.name,
                targetRatio: layer.targetRatio,
                actualValue: value
            ))
        }

        return (layersData, totalValue)
    }

    // MARK: - Export

    struct ExportData: Codable {
        let exportDate: String
        let layers: [ExportLayer]
        let holdings: [ExportHolding]
        let snapshots: [ExportSnapshot]
        let transactions: [ExportTransaction]
    }

    struct ExportLayer: Codable {
        let name: String
        let description: String
        let targetRatio: Double
        let color: String
        let order: Int
    }

    struct ExportHolding: Codable {
        let layerOrder: Int
        let name: String
        let code: String
        let assetType: String
        let quantity: Double
        let costPrice: Double?
        let currentPrice: Double?
        let marketValue: Double
        let profitLoss: Double
        let profitLossPct: Double
        let source: String
        let platform: String
        let notes: String
    }

    struct ExportSnapshot: Codable {
        let date: String
        let totalValue: Double
        let layerValues: [String: Double]
        let layerRatios: [String: Double]
        let notes: String
    }

    struct ExportTransaction: Codable {
        let action: String
        let assetName: String
        let quantity: Double
        let price: Double
        let amount: Double
        let date: String
        let notes: String
    }

    static func exportAll(context: ModelContext) -> Data? {
        let layers = (try? context.fetch(FetchDescriptor<AssetLayer>(sortBy: [SortDescriptor(\.order)]))) ?? []
        let holdings = (try? context.fetch(FetchDescriptor<Holding>())) ?? []
        let snapshots = (try? context.fetch(FetchDescriptor<Snapshot>(sortBy: [SortDescriptor(\.date, order: .reverse)]))) ?? []
        let transactions = (try? context.fetch(FetchDescriptor<Transaction>(sortBy: [SortDescriptor(\.date, order: .reverse)]))) ?? []

        let formatter = ISO8601DateFormatter()

        let export = ExportData(
            exportDate: formatter.string(from: Date()),
            layers: layers.map { ExportLayer(name: $0.name, description: $0.layerDescription, targetRatio: $0.targetRatio, color: $0.color, order: $0.order) },
            holdings: holdings.map { h in
                ExportHolding(layerOrder: h.layer?.order ?? 1, name: h.name, code: h.code, assetType: h.assetTypeRaw, quantity: h.quantity, costPrice: h.costPrice, currentPrice: h.currentPrice, marketValue: h.marketValue, profitLoss: h.profitLoss, profitLossPct: h.profitLossPct, source: h.sourceRaw, platform: h.platform, notes: h.notes)
            },
            snapshots: snapshots.map { s in
                ExportSnapshot(date: formatter.string(from: s.date), totalValue: s.totalValue, layerValues: s.layerValues, layerRatios: s.layerRatios, notes: s.notes)
            },
            transactions: transactions.map { t in
                ExportTransaction(action: t.actionRaw, assetName: t.assetName, quantity: t.quantity, price: t.price, amount: t.amount, date: formatter.string(from: t.date), notes: t.notes)
            }
        )

        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        return try? encoder.encode(export)
    }

    static func importData(_ data: Data, context: ModelContext) throws {
        let decoder = JSONDecoder()
        let importData = try decoder.decode(ExportData.self, from: data)
        let formatter = ISO8601DateFormatter()

        // Import layers
        for ld in importData.layers {
            let descriptor = FetchDescriptor<AssetLayer>(predicate: #Predicate { $0.order == ld.order })
            if let existing = try? context.fetch(descriptor).first {
                existing.name = ld.name
                existing.layerDescription = ld.description
                existing.targetRatio = ld.targetRatio
                existing.color = ld.color
            } else {
                context.insert(AssetLayer(name: ld.name, description: ld.description, targetRatio: ld.targetRatio, color: ld.color, order: ld.order))
            }
        }
        try context.save()

        // Import holdings
        let layerDescriptor = FetchDescriptor<AssetLayer>(sortBy: [SortDescriptor(\.order)])
        let layers = try context.fetch(layerDescriptor)

        for hd in importData.holdings {
            guard let layer = layers.first(where: { $0.order == hd.layerOrder }) else { continue }
            let holding = Holding(
                layer: layer, name: hd.name, code: hd.code,
                assetType: AssetType(rawValue: hd.assetType) ?? .other,
                quantity: hd.quantity, costPrice: hd.costPrice, currentPrice: hd.currentPrice,
                marketValue: hd.marketValue, profitLoss: hd.profitLoss, profitLossPct: hd.profitLossPct,
                source: HoldingSource(rawValue: hd.source) ?? .manual,
                platform: hd.platform, notes: hd.notes
            )
            context.insert(holding)
        }

        // Import snapshots
        for sd in importData.snapshots {
            let snapshot = Snapshot(
                date: formatter.date(from: sd.date) ?? Date(),
                totalValue: sd.totalValue,
                layerValues: sd.layerValues,
                layerRatios: sd.layerRatios,
                notes: sd.notes
            )
            context.insert(snapshot)
        }

        // Import transactions
        for td in importData.transactions {
            let tx = Transaction(
                action: TransactionAction(rawValue: td.action) ?? .buy,
                assetName: td.assetName,
                quantity: td.quantity,
                price: td.price,
                amount: td.amount,
                date: formatter.date(from: td.date) ?? Date(),
                notes: td.notes
            )
            context.insert(tx)
        }

        try context.save()
    }
}
