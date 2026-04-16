import Foundation
import SwiftData

struct PerformanceResult {
    let startDate: Date
    let endDate: Date
    let startValue: Double
    let endValue: Double
    let netCashflow: Double
    let absoluteProfit: Double
    let returnRatePct: Double
}

enum PerformanceService {
    /// Modified Dietz method for interval return calculation
    static func calculateIntervalPerformance(startDate: Date, endDate: Date, context: ModelContext) -> PerformanceResult {
        let empty = PerformanceResult(startDate: startDate, endDate: endDate, startValue: 0, endValue: 0, netCashflow: 0, absoluteProfit: 0, returnRatePct: 0)

        // 1. Find start snapshot (closest before or at startDate)
        var startDescriptor = FetchDescriptor<Snapshot>(
            predicate: #Predicate { $0.date <= startDate },
            sortBy: [SortDescriptor(\.date, order: .reverse)]
        )
        startDescriptor.fetchLimit = 1
        let startSnapshots = (try? context.fetch(startDescriptor)) ?? []

        let startValue: Double
        let actualStartDate: Date

        if let startSnapshot = startSnapshots.first {
            startValue = startSnapshot.totalValue
            actualStartDate = startSnapshot.date
        } else {
            // No snapshot before start, try first in range
            var firstInRange = FetchDescriptor<Snapshot>(
                predicate: #Predicate<Snapshot> { snapshot in
                    snapshot.date > startDate && snapshot.date <= endDate
                },
                sortBy: [SortDescriptor(\.date)]
            )
            firstInRange.fetchLimit = 1
            guard let first = (try? context.fetch(firstInRange))?.first else {
                return empty
            }
            startValue = first.totalValue
            actualStartDate = first.date
        }

        // 2. Find end snapshot
        var endDescriptor = FetchDescriptor<Snapshot>(
            predicate: #Predicate { $0.date <= endDate },
            sortBy: [SortDescriptor(\.date, order: .reverse)]
        )
        endDescriptor.fetchLimit = 1
        guard let endSnapshot = (try? context.fetch(endDescriptor))?.first else {
            return empty
        }

        let endValue = endSnapshot.totalValue
        let actualEndDate = endSnapshot.date

        let totalDays = Calendar.current.dateComponents([.day], from: actualStartDate, to: actualEndDate).day ?? 0
        if totalDays < 0 { return empty }

        // 3. Get cash flow transactions in range
        let txDescriptor = FetchDescriptor<Transaction>(
            predicate: #Predicate<Transaction> { tx in
                tx.date > actualStartDate && tx.date <= actualEndDate &&
                (tx.actionRaw == "transfer" || tx.actionRaw == "withdraw")
            }
        )
        let transactions = (try? context.fetch(txDescriptor)) ?? []

        var cfNet: Double = 0
        var weightedCF: Double = 0

        for tx in transactions {
            let daysPassed = max(0, min(
                Calendar.current.dateComponents([.day], from: actualStartDate, to: tx.date).day ?? 0,
                totalDays
            ))
            let weight = totalDays > 0 ? Double(totalDays - daysPassed) / Double(totalDays) : 1.0
            let amount = tx.action == .transfer ? tx.amount : -tx.amount
            cfNet += amount
            weightedCF += amount * weight
        }

        // 4. Calculate Modified Dietz return
        let profit = endValue - startValue - cfNet
        let adjustedCost = startValue + weightedCF

        let returnRate: Double
        if adjustedCost > 0 {
            returnRate = profit / adjustedCost
        } else if adjustedCost < 0 {
            returnRate = profit / abs(adjustedCost)
        } else {
            returnRate = 0
        }

        return PerformanceResult(
            startDate: actualStartDate,
            endDate: actualEndDate,
            startValue: startValue,
            endValue: endValue,
            netCashflow: cfNet,
            absoluteProfit: profit,
            returnRatePct: returnRate * 100
        )
    }
}
