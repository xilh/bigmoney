import Foundation
import SwiftData

@Model
final class Snapshot {
    var date: Date = Date()
    var totalValue: Double = 0
    var layerValuesData: Data?
    var layerRatiosData: Data?
    var notes: String = ""
    var createdAt: Date = Date()

    init(date: Date = Date(), totalValue: Double = 0,
         layerValues: [String: Double] = [:], layerRatios: [String: Double] = [:],
         notes: String = "") {
        self.date = date
        self.totalValue = totalValue
        self.layerValues = layerValues
        self.layerRatios = layerRatios
        self.notes = notes
    }

    var layerValues: [String: Double] {
        get {
            guard let data = layerValuesData else { return [:] }
            return (try? JSONDecoder().decode([String: Double].self, from: data)) ?? [:]
        }
        set {
            layerValuesData = try? JSONEncoder().encode(newValue)
        }
    }

    var layerRatios: [String: Double] {
        get {
            guard let data = layerRatiosData else { return [:] }
            return (try? JSONDecoder().decode([String: Double].self, from: data)) ?? [:]
        }
        set {
            layerRatiosData = try? JSONEncoder().encode(newValue)
        }
    }
}
