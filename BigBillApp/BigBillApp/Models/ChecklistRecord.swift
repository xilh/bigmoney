import Foundation
import SwiftData

enum PeriodType: String, Codable, CaseIterable {
    case weekly = "weekly"
    case monthly = "monthly"
    case quarterly = "quarterly"
    case yearly = "yearly"

    var displayName: String {
        switch self {
        case .weekly: return "周检视"
        case .monthly: return "月检视"
        case .quarterly: return "季度检视"
        case .yearly: return "年度检视"
        }
    }

    var periodDescription: String {
        switch self {
        case .weekly: return "每周日晚间 · 约10分钟"
        case .monthly: return "每月第一个周末 · 约30分钟"
        case .quarterly: return "季末最后一周 · 约1-2小时"
        case .yearly: return "12月或次年1月初 · 约2-3小时"
        }
    }

    var items: [String] {
        switch self {
        case .weekly:
            return [
                "查看各账户总体净值变化",
                "确认本周定投扣款是否成功执行（DCA阶段）",
                "浏览宏观新闻标题，判断是否有重大事件",
                "提醒：周检视不做任何买卖操作",
            ]
        case .monthly:
            return [
                "记录各层级实际比例 vs 目标比例",
                "检查货币基金收益率是否异常偏低",
                "确认债券基金是否有异常波动（单月跌幅>1%即为异常）",
                "审视单只个股仓位是否突破5%红线",
                "如有超5%个股，本月内分批减仓至目标比例",
            ]
        case .quarterly:
            return [
                "全面审视五层配置比例，判断是否需要再平衡",
                "检查各基金产品同类排名（连续两季后25%应考虑替换）",
                "审视行业暴露：检查股票持仓是否在某一行业过度集中",
                "审视第五层卫星仓位每笔投资逻辑是否仍然成立",
                "记录本季度总回报和各层级回报",
            ]
        case .yearly:
            return [
                "各层级实际比例 vs. 目标比例，执行强制再平衡",
                "单只个股是否有超过5%的情况",
                "基金产品同类排名审视，替换连续落后的基金",
                "保险保障是否充足，受益人是否正确",
                "税务优化：股息持有期、个税扣除项是否充分利用",
                "第五层卫星仓位每笔投资逻辑重新评估",
                "风险偏好是否需要调整（家庭、事业、健康变化）",
                "下一年度目标配置比例是否需要微调（年龄因素）",
                "遗嘱、家族信托、子女教育基金进展审视",
                "记录本年度总回报、各层级回报、重大决策日志",
            ]
        }
    }
}

@Model
final class ChecklistRecord {
    var periodTypeRaw: String = "weekly"
    var completedItemsData: Data?
    var totalItems: Int = 0
    var completedAt: Date = Date()
    var notes: String = ""

    init(periodType: PeriodType, completedItems: [String] = [], totalItems: Int = 0, notes: String = "") {
        self.periodTypeRaw = periodType.rawValue
        self.completedItems = completedItems
        self.totalItems = totalItems
        self.notes = notes
    }

    var periodType: PeriodType {
        get { PeriodType(rawValue: periodTypeRaw) ?? .weekly }
        set { periodTypeRaw = newValue.rawValue }
    }

    var completedItems: [String] {
        get {
            guard let data = completedItemsData else { return [] }
            return (try? JSONDecoder().decode([String].self, from: data)) ?? []
        }
        set {
            completedItemsData = try? JSONEncoder().encode(newValue)
        }
    }
}
