import Foundation
import UIKit

/// Recognized holding from OCR
struct RecognizedHolding: Identifiable, Codable {
    let id = UUID()
    var name: String
    var code: String
    var assetType: String
    var quantity: Double
    var costPrice: Double?
    var currentPrice: Double?
    var marketValue: Double
    var profitLoss: Double
    var profitLossPct: Double
    var suggestedLayer: Int

    enum CodingKeys: String, CodingKey {
        case name, code
        case assetType = "asset_type"
        case quantity
        case costPrice = "cost_price"
        case currentPrice = "current_price"
        case marketValue = "market_value"
        case profitLoss = "profit_loss"
        case profitLossPct = "profit_loss_pct"
        case suggestedLayer = "suggested_layer"
    }
}

struct OCRResult {
    var success: Bool
    var holdings: [RecognizedHolding]
    var platform: String
    var error: String
}

actor OCRService {
    static let shared = OCRService()

    private let recognitionPrompt = """
    你是一个专业的金融数据提取助手。请仔细分析这张持仓/理财截图，提取所有资产持仓信息。

    首先，请根据截图的 UI 界面特征判断这是哪个 App/平台的截图（如：招商银行、支付宝、天天基金、蛋卷基金、雪球、同花顺、东方财富、微信理财通、京东金融、中国银行等）。

    请返回一个 JSON 对象，格式如下：
    {
      "platform": "识别出的App名称（如截图来源不确定则填空字符串）",
      "holdings": [
        {每个持仓的信息}
      ]
    }

    每个持仓包含以下字段：
    - name: 资产名称
    - code: 资产代码（如截图中没有则留空字符串）
    - asset_type: 资产类型，必须是以下之一：cash, money_fund, bank_product, deposit, bond_fund, convertible_bond, index_fund, stock, etf, dividend_stock, gold, qdii, hk_stock, other
    - quantity: 数量/份额（数字，如截图中没有则为0）
    - cost_price: 成本价/买入均价（数字，如截图中没有则为null）
    - current_price: 当前价/最新净值（数字，如截图中没有则为null）
    - market_value: 市值/金额（数字，单位：元）
    - profit_loss: 累计盈亏金额（数字，单位：元）
    - profit_loss_pct: 盈亏比例（数字，百分比，如截图中没有则为0）
    - suggested_layer: 建议所属层级(1-5)

    ⚠️ 关键提取规则：
    1. "金额"/"持有金额"/"市值" → market_value
    2. "持有收益"/"累计收益"/"浮动盈亏" → profit_loss
    3. "昨日收益"/"日收益" → 忽略
    4. 精确提取所有数字，不要四舍五入
    5. 逗号是千分位分隔符，不是小数点
    6. 金额单位统一为人民币元
    7. 只返回 JSON 对象，不要包含其他文字
    """

    func recognizeScreenshot(imageData: Data, provider: String, apiKey: String,
                             apiURL: String = "", model: String = "", maxTokens: Int = 2048) async -> OCRResult {
        guard let image = UIImage(data: imageData) else {
            return OCRResult(success: false, holdings: [], platform: "", error: "无法读取图片")
        }

        // Compress image
        let compressed = compressImage(image, maxDimension: 800)
        let base64 = compressed.base64EncodedString()

        do {
            let responseText: String
            if provider == "anthropic" {
                responseText = try await callAnthropic(imageBase64: base64, apiKey: apiKey, model: model.isEmpty ? "claude-sonnet-4-20250514" : model, maxTokens: maxTokens)
            } else {
                responseText = try await callOpenAICompatible(imageBase64: base64, apiKey: apiKey, apiURL: apiURL, model: model.isEmpty ? "gpt-4o" : model, maxTokens: maxTokens)
            }

            let result = try extractJSON(from: responseText)
            return OCRResult(success: true, holdings: result.holdings, platform: result.platform, error: "")
        } catch {
            return OCRResult(success: false, holdings: [], platform: "", error: "识别出错: \(error.localizedDescription)")
        }
    }

    private func compressImage(_ image: UIImage, maxDimension: CGFloat) -> Data {
        var img = image
        let longest = max(img.size.width, img.size.height)
        if longest > maxDimension {
            let ratio = maxDimension / longest
            let newSize = CGSize(width: img.size.width * ratio, height: img.size.height * ratio)
            UIGraphicsBeginImageContextWithOptions(newSize, true, 1.0)
            img.draw(in: CGRect(origin: .zero, size: newSize))
            img = UIGraphicsGetImageFromCurrentImageContext() ?? img
            UIGraphicsEndImageContext()
        }
        return img.jpegData(compressionQuality: 0.75) ?? Data()
    }

    private func callAnthropic(imageBase64: String, apiKey: String, model: String, maxTokens: Int) async throws -> String {
        let url = URL(string: "https://api.anthropic.com/v1/messages")!
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue(apiKey, forHTTPHeaderField: "x-api-key")
        request.setValue("2023-06-01", forHTTPHeaderField: "anthropic-version")
        request.timeoutInterval = 300

        let body: [String: Any] = [
            "model": model,
            "max_tokens": maxTokens,
            "messages": [
                [
                    "role": "user",
                    "content": [
                        [
                            "type": "image",
                            "source": [
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": imageBase64,
                            ]
                        ],
                        [
                            "type": "text",
                            "text": recognitionPrompt,
                        ]
                    ]
                ]
            ]
        ]

        request.httpBody = try JSONSerialization.data(withJSONObject: body)
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let httpResponse = response as? HTTPURLResponse, httpResponse.statusCode == 200 else {
            let errorText = String(data: data, encoding: .utf8) ?? "Unknown error"
            throw NSError(domain: "OCR", code: -1, userInfo: [NSLocalizedDescriptionKey: "API 调用失败: \(errorText)"])
        }

        let json = try JSONSerialization.jsonObject(with: data) as? [String: Any]
        let content = (json?["content"] as? [[String: Any]])?.first?["text"] as? String ?? ""
        return content
    }

    private func callOpenAICompatible(imageBase64: String, apiKey: String, apiURL: String, model: String, maxTokens: Int) async throws -> String {
        var urlString = apiURL.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        if !urlString.hasSuffix("/chat/completions") {
            if urlString.hasSuffix("/v1") {
                urlString += "/chat/completions"
            } else {
                urlString += "/v1/chat/completions"
            }
        }

        guard let url = URL(string: urlString) else {
            throw NSError(domain: "OCR", code: -1, userInfo: [NSLocalizedDescriptionKey: "无效的 API 地址"])
        }

        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if !apiKey.isEmpty {
            request.setValue("Bearer \(apiKey)", forHTTPHeaderField: "Authorization")
        }
        request.timeoutInterval = 300

        let dataURL = "data:image/jpeg;base64,\(imageBase64)"
        let body: [String: Any] = [
            "model": model,
            "max_tokens": maxTokens,
            "messages": [
                [
                    "role": "user",
                    "content": [
                        ["type": "image_url", "image_url": ["url": dataURL]],
                        ["type": "text", "text": recognitionPrompt],
                    ]
                ]
            ]
        ]

        request.httpBody = try JSONSerialization.data(withJSONObject: body)
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let httpResponse = response as? HTTPURLResponse, httpResponse.statusCode == 200 else {
            let errorText = String(data: data, encoding: .utf8) ?? "Unknown error"
            throw NSError(domain: "OCR", code: -1, userInfo: [NSLocalizedDescriptionKey: "API 调用失败: \(errorText)"])
        }

        let json = try JSONSerialization.jsonObject(with: data) as? [String: Any]
        let choices = json?["choices"] as? [[String: Any]]
        let message = choices?.first?["message"] as? [String: Any]
        var content = message?["content"] as? String ?? ""
        if content.isEmpty {
            content = message?["reasoning_content"] as? String ?? ""
        }
        return content
    }

    private func extractJSON(from text: String) throws -> (platform: String, holdings: [RecognizedHolding]) {
        var jsonText = text.trimmingCharacters(in: .whitespacesAndNewlines)

        // Try to extract from markdown code block
        if let range = jsonText.range(of: "```(?:json)?\\s*([\\s\\S]*?)\\s*```", options: .regularExpression) {
            let match = String(jsonText[range])
            jsonText = match
                .replacingOccurrences(of: "```json", with: "")
                .replacingOccurrences(of: "```", with: "")
                .trimmingCharacters(in: .whitespacesAndNewlines)
        } else {
            // Find outermost { or [
            if let startObj = jsonText.firstIndex(of: "{") {
                if let endObj = jsonText.lastIndex(of: "}") {
                    jsonText = String(jsonText[startObj...endObj])
                }
            } else if let startArr = jsonText.firstIndex(of: "[") {
                if let endArr = jsonText.lastIndex(of: "]") {
                    jsonText = String(jsonText[startArr...endArr])
                }
            }
        }

        guard let data = jsonText.data(using: .utf8) else {
            throw NSError(domain: "OCR", code: -1, userInfo: [NSLocalizedDescriptionKey: "无法解析 JSON"])
        }

        // Try to parse
        let parsed = try JSONSerialization.jsonObject(with: data)

        if let dict = parsed as? [String: Any] {
            let platform = dict["platform"] as? String ?? ""
            if let holdingsArray = dict["holdings"] as? [[String: Any]] {
                let holdingsData = try JSONSerialization.data(withJSONObject: holdingsArray)
                let decoder = JSONDecoder()
                let holdings = (try? decoder.decode([RecognizedHolding].self, from: holdingsData)) ?? []
                return (platform, holdings)
            }
        } else if let arr = parsed as? [[String: Any]] {
            let holdingsData = try JSONSerialization.data(withJSONObject: arr)
            let holdings = (try? JSONDecoder().decode([RecognizedHolding].self, from: holdingsData)) ?? []
            return ("", holdings)
        }

        throw NSError(domain: "OCR", code: -1, userInfo: [NSLocalizedDescriptionKey: "无法解析识别结果"])
    }
}
