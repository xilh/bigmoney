import Foundation

struct LayerData {
    let id: Int
    let name: String
    let targetRatio: Double
    let actualValue: Double
}

struct LayerResult {
    let id: Int
    let name: String
    let targetRatio: Double
    let actualRatio: Double
    let actualValue: Double
    let targetValue: Double
    let deviation: Double
    let adjustment: Double
    let status: String // "balanced", "warning", "critical"
}

struct RebalanceAlert: Identifiable {
    let id = UUID()
    let level: String // "critical", "warning"
    let layer: String
    let message: String
    let action: String
}

struct RebalanceSuggestion: Identifiable {
    let id = UUID()
    let type: String // "buy", "sell"
    let layer: String
    let amount: Double
    let message: String
}

struct RebalanceResult {
    let totalValue: Double
    let layers: [LayerResult]
    let alerts: [RebalanceAlert]
    let suggestions: [RebalanceSuggestion]
}

struct AllocationSuggestion: Identifiable {
    let id = UUID()
    let layer: String
    let layerID: Int
    let amount: Double
    let reason: String
}

enum RebalanceService {
    static func calculate(layersData: [LayerData], totalValue: Double) -> RebalanceResult {
        guard totalValue > 0 else {
            return RebalanceResult(totalValue: 0, layers: [], alerts: [], suggestions: [])
        }

        var results: [LayerResult] = []
        var alerts: [RebalanceAlert] = []
        var suggestions: [RebalanceSuggestion] = []

        for layer in layersData {
            let actualRatio = layer.actualValue / totalValue * 100
            let deviation = actualRatio - layer.targetRatio
            let targetValue = totalValue * layer.targetRatio / 100
            let adjustment = layer.actualValue - targetValue

            let status: String
            if abs(deviation) > 5 {
                status = "critical"
                alerts.append(RebalanceAlert(
                    level: "critical",
                    layer: layer.name,
                    message: "\(layer.name)偏差 \(String(format: "%+.1f", deviation))%，超过5%阈值，建议立即调整",
                    action: "\(adjustment > 0 ? "卖出" : "买入") \(CurrencyFormatter.format(abs(adjustment)))"
                ))
            } else if abs(deviation) > 3 {
                status = "warning"
                alerts.append(RebalanceAlert(
                    level: "warning",
                    layer: layer.name,
                    message: "\(layer.name)偏差 \(String(format: "%+.1f", deviation))%，接近阈值，关注中",
                    action: "可在年度再平衡时处理"
                ))
            } else {
                status = "balanced"
            }

            results.append(LayerResult(
                id: layer.id, name: layer.name,
                targetRatio: layer.targetRatio,
                actualRatio: (actualRatio * 100).rounded() / 100,
                actualValue: layer.actualValue,
                targetValue: (targetValue * 100).rounded() / 100,
                deviation: (deviation * 100).rounded() / 100,
                adjustment: (adjustment * 100).rounded() / 100,
                status: status
            ))
        }

        // Generate suggestions
        let overLayers = results.filter { $0.adjustment > 0 }.sorted { $0.adjustment > $1.adjustment }
        let underLayers = results.filter { $0.adjustment < 0 }.sorted { $0.adjustment < $1.adjustment }

        for layer in overLayers {
            suggestions.append(RebalanceSuggestion(
                type: "sell", layer: layer.name, amount: layer.adjustment,
                message: "从\(layer.name)减持 \(CurrencyFormatter.format(layer.adjustment))"
            ))
        }
        for layer in underLayers {
            suggestions.append(RebalanceSuggestion(
                type: "buy", layer: layer.name, amount: abs(layer.adjustment),
                message: "向\(layer.name)增持 \(CurrencyFormatter.format(abs(layer.adjustment)))"
            ))
        }

        return RebalanceResult(totalValue: totalValue, layers: results, alerts: alerts, suggestions: suggestions)
    }

    static func allocateNewFunds(layersData: [LayerData], totalValue: Double, newAmount: Double) -> [AllocationSuggestion] {
        guard newAmount > 0, totalValue > 0 else { return [] }

        let newTotal = totalValue + newAmount
        var allocations: [AllocationSuggestion] = []
        var remaining = newAmount

        // Calculate gaps
        var gaps: [(id: Int, name: String, gap: Double, targetRatio: Double)] = []
        for layer in layersData {
            let targetValue = newTotal * layer.targetRatio / 100
            let gap = targetValue - layer.actualValue
            if gap > 0 {
                gaps.append((layer.id, layer.name, gap, layer.targetRatio))
            }
        }

        gaps.sort { $0.gap > $1.gap }

        for gap in gaps {
            guard remaining > 0 else { break }
            let alloc = min(gap.gap, remaining)
            allocations.append(AllocationSuggestion(
                layer: gap.name, layerID: gap.id, amount: (alloc * 100).rounded() / 100,
                reason: "当前低配，缺口 \(CurrencyFormatter.format(gap.gap))"
            ))
            remaining -= alloc
        }

        if remaining > 0 {
            for layer in layersData {
                let alloc = remaining * layer.targetRatio / 100
                if alloc > 0 {
                    if let idx = allocations.firstIndex(where: { $0.layerID == layer.id }) {
                        let existing = allocations[idx]
                        allocations[idx] = AllocationSuggestion(
                            layer: existing.layer, layerID: existing.layerID,
                            amount: existing.amount + (alloc * 100).rounded() / 100,
                            reason: existing.reason
                        )
                    } else {
                        allocations.append(AllocationSuggestion(
                            layer: layer.name, layerID: layer.id,
                            amount: (alloc * 100).rounded() / 100,
                            reason: "按目标比例分配剩余资金"
                        ))
                    }
                }
            }
        }

        return allocations
    }

    static let drawdownProtocols: [(name: String, rules: [(threshold: String, action: String)])] = [
        (
            name: "第一层/第二层（安全垫 + 债券）",
            rules: [
                ("单月跌幅 >1%", "检查原因。如为利率政策冲击（央行加息），继续持有。可考虑缩短久期，换入更短期限的债基。"),
                ("单月跌幅 >3%", "可能信用事件或极端利率环境。审视基金持仓是否有信用违约风险，如有则立即转换为利率债基金或货币基金。"),
            ]
        ),
        (
            name: "第三层（股票核心）",
            rules: [
                ("跌幅 0–10%", "正常波动，市场噪音。不做任何操作，DCA阶段继续执行定投。"),
                ("跌幅 10–20%", "明显调整，但仍属常见范围。检查各层级偏差，如触发5%偏差则执行触发式再平衡。"),
                ("跌幅 20–30%", "显著回撤，A股每3–5年会发生一次。动用第一层「干火药」储备加仓指数基金，分三批投入。"),
                ("跌幅 >30%", "极端恐慌，历史性买入机会。将第五层现金也加仓至指数基金。「别人恐惧我贪婪」。"),
            ]
        ),
        (
            name: "第四层（黄金/港股）",
            rules: [
                ("黄金单独下跌 15–20%", "正常波动。持有不动。黄金的价值在于与股票的低相关性。"),
                ("黄金与股票同时下跌", "可能是流动性危机。继续持有，黄金通常最先反弹。"),
                ("黄金跌而股票涨", "正常的资产轮动，组合正在按设计运行。不需操作。"),
            ]
        ),
        (
            name: "第五层（卫星仓位）",
            rules: [
                ("单笔投资跌 40%+", "问自己：投资逻辑是否已变？如果没变→继续持有或小幅加仓。如果变了→果断止损。"),
                ("单笔投资跌 70%+", "基本确认论点失败。止损或接受为「学费」，不要补仓摊平成本。"),
            ]
        ),
    ]
}
