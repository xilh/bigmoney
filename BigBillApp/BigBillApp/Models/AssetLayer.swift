import Foundation
import SwiftData

@Model
final class AssetLayer {
    var name: String = ""
    var layerDescription: String = ""
    var targetRatio: Double = 0
    var color: String = "#3b82f6"
    var order: Int = 0
    var createdAt: Date = Date()

    @Relationship(deleteRule: .cascade, inverse: \Holding.layer)
    var holdings: [Holding] = []

    init(name: String, description: String = "", targetRatio: Double = 0, color: String = "#3b82f6", order: Int = 0) {
        self.name = name
        self.layerDescription = description
        self.targetRatio = targetRatio
        self.color = color
        self.order = order
        self.createdAt = Date()
    }

    var totalMarketValue: Double {
        holdings.reduce(0) { $0 + $1.marketValue }
    }

    var totalProfitLoss: Double {
        holdings.reduce(0) { $0 + $1.profitLoss }
    }

    static func seedLayers() -> [AssetLayer] {
        [
            AssetLayer(
                name: "第一层·安全垫",
                description: "应急储备 + 机会资金。货币基金、银行理财(R1-R2)、大额存单。预期收益约2-2.5%。",
                targetRatio: 12.5,
                color: "hsl(152, 65%, 50%)",
                order: 1
            ),
            AssetLayer(
                name: "第二层·债券核心",
                description: "稳健收益，抵抗股市波动。中短债基金、纯债基金、可转债基金。预期年化3-5%。",
                targetRatio: 22.5,
                color: "hsl(200, 80%, 55%)",
                order: 2
            ),
            AssetLayer(
                name: "第三层·股票核心",
                description: "长期资本增值主引擎。沪深300ETF、红利股、优质价值成长股。",
                targetRatio: 37.5,
                color: "hsl(35, 90%, 55%)",
                order: 3
            ),
            AssetLayer(
                name: "第四层·另类对冲",
                description: "不相关收益来源、通胀对冲。黄金ETF(5-8%)、港股/QDII(5-7%)。",
                targetRatio: 12.5,
                color: "hsl(45, 95%, 55%)",
                order: 4
            ),
            AssetLayer(
                name: "第五层·卫星机会",
                description: "战术性机会、新兴主题、学习成长。行业ETF、打新、主题投资。",
                targetRatio: 15.0,
                color: "hsl(300, 65%, 55%)",
                order: 5
            ),
        ]
    }
}
