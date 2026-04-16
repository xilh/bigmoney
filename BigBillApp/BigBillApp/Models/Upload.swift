import Foundation
import SwiftData

enum UploadStatus: String, Codable {
    case pending = "pending"
    case processing = "processing"
    case recognized = "recognized"
    case confirmed = "confirmed"
    case failed = "failed"

    var displayName: String {
        switch self {
        case .pending: return "待识别"
        case .processing: return "识别中"
        case .recognized: return "已识别"
        case .confirmed: return "已确认"
        case .failed: return "识别失败"
        }
    }
}

@Model
final class Upload {
    var imageData: Data?
    var platform: String = ""
    var recognizedDataJSON: Data?
    var statusRaw: String = "pending"
    var errorMessage: String = ""
    var createdAt: Date = Date()

    init(imageData: Data? = nil, platform: String = "", status: UploadStatus = .pending) {
        self.imageData = imageData
        self.platform = platform
        self.statusRaw = status.rawValue
    }

    var status: UploadStatus {
        get { UploadStatus(rawValue: statusRaw) ?? .pending }
        set { statusRaw = newValue.rawValue }
    }

    var recognizedData: [[String: Any]] {
        get {
            guard let data = recognizedDataJSON else { return [] }
            return (try? JSONSerialization.jsonObject(with: data) as? [[String: Any]]) ?? []
        }
        set {
            recognizedDataJSON = try? JSONSerialization.data(withJSONObject: newValue)
        }
    }
}
