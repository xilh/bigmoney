import SwiftUI

// MARK: - Color from hex/HSL

extension Color {
    init(hex: String) {
        let hex = hex.trimmingCharacters(in: CharacterSet(charactersIn: "#"))
        var int: UInt64 = 0
        Scanner(string: hex).scanHexInt64(&int)
        let r, g, b: Double
        switch hex.count {
        case 6:
            r = Double((int >> 16) & 0xFF) / 255.0
            g = Double((int >> 8) & 0xFF) / 255.0
            b = Double(int & 0xFF) / 255.0
        default:
            r = 0; g = 0; b = 0
        }
        self.init(red: r, green: g, blue: b)
    }

    static func fromLayerColor(_ colorString: String) -> Color {
        if colorString.hasPrefix("#") {
            return Color(hex: colorString)
        }
        // Parse hsl(h, s%, l%)
        if colorString.hasPrefix("hsl") {
            let nums = colorString
                .replacingOccurrences(of: "hsl(", with: "")
                .replacingOccurrences(of: ")", with: "")
                .split(separator: ",")
                .compactMap { Double($0.trimmingCharacters(in: .whitespaces).replacingOccurrences(of: "%", with: "")) }
            if nums.count == 3 {
                return Color(hue: nums[0] / 360, saturation: nums[1] / 100, brightness: nums[2] / 100 + 0.15)
            }
        }
        return .blue
    }

    // Layer colors for the 5 tiers
    static let layer1 = Color(hue: 152/360, saturation: 0.65, brightness: 0.65)
    static let layer2 = Color(hue: 200/360, saturation: 0.80, brightness: 0.70)
    static let layer3 = Color(hue: 35/360, saturation: 0.90, brightness: 0.70)
    static let layer4 = Color(hue: 45/360, saturation: 0.95, brightness: 0.70)
    static let layer5 = Color(hue: 300/360, saturation: 0.65, brightness: 0.70)

    static func layerColor(for order: Int) -> Color {
        switch order {
        case 1: return .layer1
        case 2: return .layer2
        case 3: return .layer3
        case 4: return .layer4
        case 5: return .layer5
        default: return .blue
        }
    }
}

// MARK: - Number formatting

struct CurrencyFormatter {
    static func format(_ value: Double, showSign: Bool = false) -> String {
        let absValue = abs(value)
        let prefix = showSign ? (value >= 0 ? "+" : "-") : (value < 0 ? "-" : "")

        if absValue >= 100_000_000 {
            return "\(prefix)¥\(String(format: "%.2f", absValue / 100_000_000))亿"
        } else if absValue >= 10_000 {
            return "\(prefix)¥\(String(format: "%.2f", absValue / 10_000))万"
        } else {
            return "\(prefix)¥\(String(format: "%.2f", absValue))"
        }
    }

    static func formatPercent(_ value: Double, showSign: Bool = true) -> String {
        let sign = showSign ? (value >= 0 ? "+" : "") : ""
        return "\(sign)\(String(format: "%.2f", value))%"
    }
}

// MARK: - Date formatting

extension Date {
    var shortString: String {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd"
        return f.string(from: self)
    }

    var fullString: String {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd HH:mm"
        return f.string(from: self)
    }
}
