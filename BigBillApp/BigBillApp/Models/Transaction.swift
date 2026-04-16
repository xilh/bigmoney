import Foundation
import SwiftData

enum TransactionAction: String, Codable, CaseIterable {
    case buy = "buy"
    case sell = "sell"
    case dividend = "dividend"
    case rebalance = "rebalance"
    case transfer = "transfer"
    case withdraw = "withdraw"

    var displayName: String {
        switch self {
        case .buy: return "买入"
        case .sell: return "卖出"
        case .dividend: return "分红"
        case .rebalance: return "再平衡"
        case .transfer: return "转入"
        case .withdraw: return "转出"
        }
    }
}

@Model
final class Transaction {
    var actionRaw: String = "buy"
    var assetName: String = ""
    var quantity: Double = 0
    var price: Double = 0
    var amount: Double = 0
    var date: Date = Date()
    var notes: String = ""
    var createdAt: Date = Date()

    init(action: TransactionAction, assetName: String, quantity: Double = 0,
         price: Double = 0, amount: Double = 0, date: Date = Date(), notes: String = "") {
        self.actionRaw = action.rawValue
        self.assetName = assetName
        self.quantity = quantity
        self.price = price
        self.amount = amount
        self.date = date
        self.notes = notes
    }

    var action: TransactionAction {
        get { TransactionAction(rawValue: actionRaw) ?? .buy }
        set { actionRaw = newValue.rawValue }
    }
}
