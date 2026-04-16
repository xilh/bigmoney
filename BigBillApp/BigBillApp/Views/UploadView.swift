import SwiftUI
import SwiftData
import PhotosUI

struct UploadView: View {
    @Environment(\.modelContext) private var context
    @Query(sort: \AssetLayer.order) private var layers: [AssetLayer]
    @Query(sort: \Upload.createdAt, order: .reverse) private var uploads: [Upload]

    @State private var selectedPhoto: PhotosPickerItem?
    @State private var imageData: Data?
    @State private var isProcessing = false
    @State private var recognizedHoldings: [RecognizedHolding] = []
    @State private var platform = ""
    @State private var errorMessage = ""
    @State private var currentUploadID: Upload?
    @State private var showResults = false
    @State private var layerAssignments: [UUID: Int] = [:]

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 16) {
                    // Upload zone
                    uploadZone

                    // Processing indicator
                    if isProcessing {
                        ProgressView("AI 正在识别中...")
                            .padding()
                    }

                    // Error message
                    if !errorMessage.isEmpty {
                        Text(errorMessage)
                            .font(.caption)
                            .foregroundStyle(.red)
                            .padding()
                            .background(Color.red.opacity(0.1))
                            .clipShape(RoundedRectangle(cornerRadius: 8))
                    }

                    // Results
                    if showResults && !recognizedHoldings.isEmpty {
                        resultsSection
                    }

                    // Upload history
                    historySection
                }
                .padding()
            }
            .background(Color(.systemGroupedBackground))
            .navigationTitle("截图识别")
            .onChange(of: selectedPhoto) { _, newValue in
                Task {
                    if let data = try? await newValue?.loadTransferable(type: Data.self) {
                        imageData = data
                        await processImage(data)
                    }
                }
            }
        }
    }

    // MARK: - Upload Zone

    private var uploadZone: some View {
        VStack(spacing: 12) {
            if let imageData, let uiImage = UIImage(data: imageData) {
                Image(uiImage: uiImage)
                    .resizable()
                    .scaledToFit()
                    .frame(maxHeight: 200)
                    .clipShape(RoundedRectangle(cornerRadius: 8))
            }

            PhotosPicker(selection: $selectedPhoto, matching: .images) {
                VStack(spacing: 8) {
                    Image(systemName: "photo.on.rectangle.angled")
                        .font(.largeTitle)
                        .foregroundStyle(.blue)
                    Text("选择截图")
                        .font(.headline)
                    Text("从相册中选择持仓截图，AI 自动识别")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                .frame(maxWidth: .infinity)
                .padding(.vertical, 30)
                .background(.regularMaterial)
                .clipShape(RoundedRectangle(cornerRadius: 12))
            }
        }
    }

    // MARK: - Results

    private var resultsSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("识别结果")
                    .font(.headline)
                Spacer()
                Text("\(recognizedHoldings.count) 条持仓")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            if !platform.isEmpty {
                HStack {
                    Text("识别平台:")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Text(platform)
                        .font(.caption)
                        .fontWeight(.medium)
                }
            }

            // Edit platform
            TextField("平台名称", text: $platform)
                .textFieldStyle(.roundedBorder)
                .font(.subheadline)

            ForEach(recognizedHoldings) { holding in
                recognizedHoldingCard(holding)
            }

            // Confirm button
            Button {
                confirmHoldings()
            } label: {
                Text("确认并导入")
                    .frame(maxWidth: .infinity)
                    .padding()
                    .background(.blue)
                    .foregroundStyle(.white)
                    .clipShape(RoundedRectangle(cornerRadius: 10))
            }
        }
        .padding()
        .background(.regularMaterial)
        .clipShape(RoundedRectangle(cornerRadius: 12))
    }

    private func recognizedHoldingCard(_ holding: RecognizedHolding) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(holding.name)
                    .font(.subheadline)
                    .fontWeight(.medium)
                if !holding.code.isEmpty {
                    Text(holding.code)
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Text(CurrencyFormatter.format(holding.marketValue))
                    .font(.subheadline)
                    .fontWeight(.medium)
            }

            HStack {
                if holding.profitLoss != 0 {
                    Text("盈亏: \(CurrencyFormatter.format(holding.profitLoss, showSign: true))")
                        .font(.caption)
                        .foregroundStyle(holding.profitLoss >= 0 ? .green : .red)
                }

                Spacer()

                // Layer picker
                Picker("层级", selection: Binding(
                    get: { layerAssignments[holding.id] ?? holding.suggestedLayer },
                    set: { layerAssignments[holding.id] = $0 }
                )) {
                    ForEach(layers) { layer in
                        Text(layer.name).tag(layer.order)
                    }
                }
                .pickerStyle(.menu)
                .font(.caption)
            }
        }
        .padding(10)
        .background(Color(.secondarySystemGroupedBackground))
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    // MARK: - History

    private var historySection: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("识别历史")
                .font(.headline)

            if uploads.prefix(10).isEmpty {
                Text("暂无记录")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            } else {
                ForEach(Array(uploads.prefix(10))) { upload in
                    HStack {
                        Image(systemName: statusIcon(upload.status))
                            .foregroundStyle(statusColor(upload.status))
                        VStack(alignment: .leading) {
                            Text(upload.platform.isEmpty ? "截图 #\(upload.createdAt.shortString)" : upload.platform)
                                .font(.subheadline)
                            Text(upload.status.displayName)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                        Spacer()
                        Text(upload.createdAt.fullString)
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                }
            }
        }
        .padding()
        .background(.regularMaterial)
        .clipShape(RoundedRectangle(cornerRadius: 12))
    }

    // MARK: - Actions

    private func processImage(_ data: Data) async {
        isProcessing = true
        errorMessage = ""
        showResults = false
        recognizedHoldings = []

        let upload = Upload(imageData: data, status: .processing)
        context.insert(upload)
        try? context.save()
        currentUploadID = upload

        let provider = AppSetting.get("llm_provider", default: "anthropic", context: context)
        let apiKey: String
        let apiURL: String
        let model: String
        let maxTokens = Int(AppSetting.get("llm_max_tokens", default: "2048", context: context)) ?? 2048

        if provider == "anthropic" {
            apiKey = AppSetting.get("anthropic_api_key", context: context)
            apiURL = ""
            model = ""
            if apiKey.isEmpty {
                errorMessage = "未配置 Anthropic API Key，请先在设置中配置"
                upload.status = .failed
                upload.errorMessage = errorMessage
                isProcessing = false
                return
            }
        } else {
            apiKey = AppSetting.get("local_api_key", context: context)
            apiURL = AppSetting.get("local_api_url", context: context)
            model = AppSetting.get("local_model", context: context)
            if apiURL.isEmpty {
                errorMessage = "未配置本地模型 API 地址，请先在设置中配置"
                upload.status = .failed
                upload.errorMessage = errorMessage
                isProcessing = false
                return
            }
        }

        let result = await OCRService.shared.recognizeScreenshot(
            imageData: data, provider: provider, apiKey: apiKey,
            apiURL: apiURL, model: model, maxTokens: maxTokens
        )

        isProcessing = false

        if result.success {
            recognizedHoldings = result.holdings
            platform = result.platform
            showResults = true
            upload.status = .recognized
            upload.platform = result.platform
        } else {
            errorMessage = result.error
            upload.status = .failed
            upload.errorMessage = result.error
        }
        try? context.save()
    }

    private func confirmHoldings() {
        for holding in recognizedHoldings {
            let layerOrder = layerAssignments[holding.id] ?? holding.suggestedLayer
            guard let layer = layers.first(where: { $0.order == layerOrder }) ?? layers.first else { continue }

            // Check if holding with same name and platform exists
            let holdingName = holding.name
            let holdingPlatform = platform
            let descriptor = FetchDescriptor<Holding>(predicate: #Predicate {
                $0.name == holdingName && $0.platform == holdingPlatform
            })

            if let existing = try? context.fetch(descriptor).first {
                existing.layer = layer
                existing.code = holding.code
                existing.assetType = AssetType(rawValue: holding.assetType) ?? .other
                existing.quantity = holding.quantity
                existing.costPrice = holding.costPrice
                existing.currentPrice = holding.currentPrice
                existing.marketValue = holding.marketValue
                existing.profitLoss = holding.profitLoss
                existing.profitLossPct = holding.profitLossPct
                existing.source = .screenshot
            } else {
                let newHolding = Holding(
                    layer: layer, name: holding.name, code: holding.code,
                    assetType: AssetType(rawValue: holding.assetType) ?? .other,
                    quantity: holding.quantity, costPrice: holding.costPrice,
                    currentPrice: holding.currentPrice, marketValue: holding.marketValue,
                    profitLoss: holding.profitLoss, profitLossPct: holding.profitLossPct,
                    source: .screenshot, platform: platform
                )
                context.insert(newHolding)
            }
        }

        currentUploadID?.status = .confirmed
        currentUploadID?.platform = platform
        try? context.save()

        // Reset
        showResults = false
        recognizedHoldings = []
        imageData = nil
        selectedPhoto = nil
    }

    private func statusIcon(_ status: UploadStatus) -> String {
        switch status {
        case .pending: return "clock"
        case .processing: return "arrow.triangle.2.circlepath"
        case .recognized: return "checkmark.circle"
        case .confirmed: return "checkmark.circle.fill"
        case .failed: return "xmark.circle"
        }
    }

    private func statusColor(_ status: UploadStatus) -> Color {
        switch status {
        case .pending: return .gray
        case .processing: return .blue
        case .recognized: return .orange
        case .confirmed: return .green
        case .failed: return .red
        }
    }
}

#Preview {
    UploadView()
        .modelContainer(for: [AssetLayer.self, Holding.self, Snapshot.self, Transaction.self, Upload.self, ChecklistRecord.self, AppSetting.self], inMemory: true)
}
