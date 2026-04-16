import Foundation
import SwiftData

enum AssetType: String, Codable, CaseIterable {
    case cash = "cash"
    case moneyFund = "money_fund"
    case bankProduct = "bank_product"
    case deposit = "deposit"
    case bondFund = "bond_fund"
    case convertibleBond = "convertible_bond"
    case indexFund = "index_fund"
    case stock = "stock"
    case etf = "etf"
    case dividendStock = "dividend_stock"
    case gold = "gold"
    case qdii = "qdii"
    case hkStock = "hk_stock"
    case other = "other"

    var displayName: String {
        switch self {
        case .cash: return "现金"
        case .moneyFund: return "货币基金"
        case .bankProduct: return "银行理财"
        case .deposit: return "存款/大额存单"
        case .bondFund: return "债券基金"
        case .convertibleBond: return "可转债基金"
        case .indexFund: return "指数基金"
        case .stock: return "股票"
        case .etf: return "ETF"
        case .dividendStock: return "红利股"
        case .gold: return "黄金"
        case .qdii: return "QDII基金"
        case .hkStock: return "港股"
        case .other: return "其他"
        }
    }
}

enum HoldingSource: String, Codable {
    case manual = "manual"
    case screenshot = "screenshot"

    var displayName: String {
        switch self {
        case .manual: return "手动输入"
        case .screenshot: return "截图识别"
        }
    }
}

@Model
final class Holding {
    var layer: AssetLayer?
    var name: String = ""
    var code: String = ""
    var assetTypeRaw: String = "other"
    var quantity: Double = 0
    var costPrice: Double?
    var currentPrice: Double?
    var marketValue: Double = 0
    var profitLoss: Double = 0
    var profitLossPct: Double = 0
    var sourceRaw: String = "manual"
    var platform: String = ""
    var notes: String = ""
    var createdAt: Date = Date()
    var updatedAt: Date = Date()

    init(layer: AssetLayer, name: String, code: String = "", assetType: AssetType = .other,
         quantity: Double = 0, costPrice: Double? = nil, currentPrice: Double? = nil,
         marketValue: Double = 0, profitLoss: Double = 0, profitLossPct: Double = 0,
         source: HoldingSource = .manual, platform: String = "", notes: String = "") {
        self.layer = layer
        self.name = name
        self.code = code
        self.assetTypeRaw = assetType.rawValue
        self.quantity = quantity
        self.costPrice = costPrice
        self.currentPrice = currentPrice
        self.marketValue = marketValue
        self.profitLoss = profitLoss
        self.profitLossPct = profitLossPct
        self.sourceRaw = source.rawValue
        self.platform = platform
        self.notes = notes
    }

    var assetType: AssetType {
        get { AssetType(rawValue: assetTypeRaw) ?? .other }
        set { assetTypeRaw = newValue.rawValue }
    }

    var source: HoldingSource {
        get { HoldingSource(rawValue: sourceRaw) ?? .manual }
        set { sourceRaw = newValue.rawValue }
    }

    /// Auto-calculate P&L from prices if available
    func recalculate() {
        if let cost = costPrice, let current = currentPrice, quantity > 0 {
            let costTotal = cost * quantity
            marketValue = current * quantity
            profitLoss = marketValue - costTotal
            profitLossPct = costTotal > 0 ? (profitLoss / costTotal * 100) : 0
        }
        updatedAt = Date()
    }
}
