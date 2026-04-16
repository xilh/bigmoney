import Foundation
import SwiftData

@Model
final class AppSetting {
    @Attribute(.unique) var key: String = ""
    var value: String = ""

    init(key: String, value: String = "") {
        self.key = key
        self.value = value
    }

    static func get(_ key: String, default defaultValue: String = "", context: ModelContext) -> String {
        let descriptor = FetchDescriptor<AppSetting>(predicate: #Predicate { $0.key == key })
        guard let setting = try? context.fetch(descriptor).first else { return defaultValue }
        return setting.value
    }

    static func set(_ key: String, value: String, context: ModelContext) {
        let descriptor = FetchDescriptor<AppSetting>(predicate: #Predicate { $0.key == key })
        if let existing = try? context.fetch(descriptor).first {
            existing.value = value
        } else {
            context.insert(AppSetting(key: key, value: value))
        }
        try? context.save()
    }
}
