import SwiftUI
import SwiftData
import UniformTypeIdentifiers

struct SettingsView: View {
    @Environment(\.modelContext) private var context
    @Query(sort: \AssetLayer.order) private var layers: [AssetLayer]

    @State private var provider = "anthropic"
    @State private var anthropicKey = ""
    @State private var localAPIURL = ""
    @State private var localAPIKey = ""
    @State private var localModel = ""
    @State private var maxTokens = "2048"

    @State private var showExportShare = false
    @State private var exportData: Data?
    @State private var showImportPicker = false
    @State private var statusMessage = ""
    @State private var showTestResult = false
    @State private var testResultMessage = ""

    var body: some View {
        NavigationStack {
            Form {
                // LLM Configuration
                Section("LLM 配置") {
                    Picker("提供商", selection: $provider) {
                        Text("Anthropic").tag("anthropic")
                        Text("本地模型 / OpenAI 兼容").tag("openai_compatible")
                    }
                    .onChange(of: provider) { _, _ in saveSettings() }

                    if provider == "anthropic" {
                        SecureField("Anthropic API Key", text: $anthropicKey)
                            .textContentType(.password)
                    } else {
                        TextField("API 地址", text: $localAPIURL)
                            .textContentType(.URL)
                            .autocapitalization(.none)
                        SecureField("API Key（选填）", text: $localAPIKey)
                        TextField("模型名称", text: $localModel)
                            .autocapitalization(.none)
                    }

                    TextField("最大 Token 数", text: $maxTokens)
                        .keyboardType(.numberPad)

                    Button("测试连接") {
                        testConnection()
                    }

                    Button("保存配置") {
                        saveSettings()
                        statusMessage = "配置已保存"
                    }
                    .buttonStyle(.borderedProminent)
                }

                if !statusMessage.isEmpty {
                    Section {
                        Text(statusMessage)
                            .font(.caption)
                            .foregroundStyle(.green)
                    }
                }

                // Target Ratios
                Section("目标比例") {
                    ForEach(layers) { layer in
                        HStack {
                            Circle()
                                .fill(Color.layerColor(for: layer.order))
                                .frame(width: 8, height: 8)
                            Text(layer.name)
                                .font(.subheadline)
                            Spacer()
                            TextField("", value: Binding(
                                get: { layer.targetRatio },
                                set: { layer.targetRatio = $0 }
                            ), format: .number)
                            .keyboardType(.decimalPad)
                            .frame(width: 60)
                            .textFieldStyle(.roundedBorder)
                            .multilineTextAlignment(.trailing)
                            Text("%")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }

                    let totalRatio = layers.reduce(0) { $0 + $1.targetRatio }
                    HStack {
                        Text("总计")
                            .fontWeight(.medium)
                        Spacer()
                        Text("\(String(format: "%.1f", totalRatio))%")
                            .foregroundStyle(abs(totalRatio - 100) < 0.1 ? .green : .red)
                    }

                    Button("保存比例") {
                        try? context.save()
                        statusMessage = "比例已保存"
                    }
                    .buttonStyle(.borderedProminent)
                }

                // Data Management
                Section("数据管理") {
                    Button("导出全部数据") {
                        exportData = DataService.exportAll(context: context)
                        showExportShare = true
                    }

                    Button("导入数据") {
                        showImportPicker = true
                    }
                }

                // Checklist & Info (links)
                Section {
                    NavigationLink("检视清单") {
                        ChecklistView()
                    }
                }

                Section("关于") {
                    HStack {
                        Text("版本")
                        Spacer()
                        Text("1.0.0")
                            .foregroundStyle(.secondary)
                    }
                    HStack {
                        Text("BigBill 变有钱")
                        Spacer()
                        Text("个人资产配置工具")
                            .foregroundStyle(.secondary)
                    }
                }
            }
            .navigationTitle("设置")
            .onAppear { loadSettings() }
            .sheet(isPresented: $showExportShare) {
                if let data = exportData {
                    ShareSheet(items: [data])
                }
            }
            .fileImporter(isPresented: $showImportPicker, allowedContentTypes: [.json]) { result in
                handleImport(result)
            }
            .alert("测试结果", isPresented: $showTestResult) {
                Button("确定") { }
            } message: {
                Text(testResultMessage)
            }
        }
    }

    private func loadSettings() {
        provider = AppSetting.get("llm_provider", default: "anthropic", context: context)
        anthropicKey = AppSetting.get("anthropic_api_key", context: context)
        localAPIURL = AppSetting.get("local_api_url", context: context)
        localAPIKey = AppSetting.get("local_api_key", context: context)
        localModel = AppSetting.get("local_model", context: context)
        maxTokens = AppSetting.get("llm_max_tokens", default: "2048", context: context)
    }

    private func saveSettings() {
        AppSetting.set("llm_provider", value: provider, context: context)
        AppSetting.set("anthropic_api_key", value: anthropicKey, context: context)
        AppSetting.set("local_api_url", value: localAPIURL, context: context)
        AppSetting.set("local_api_key", value: localAPIKey, context: context)
        AppSetting.set("local_model", value: localModel, context: context)
        AppSetting.set("llm_max_tokens", value: maxTokens, context: context)
    }

    private func testConnection() {
        if provider == "anthropic" {
            if anthropicKey.hasPrefix("sk-ant-") {
                testResultMessage = "API Key 格式正确，将在首次截图识别时验证连接"
                showTestResult = true
            } else if anthropicKey.isEmpty {
                testResultMessage = "请输入 API Key"
                showTestResult = true
            } else {
                testResultMessage = "API Key 格式不正确，应以 sk-ant- 开头"
                showTestResult = true
            }
        } else {
            guard !localAPIURL.isEmpty else {
                testResultMessage = "请输入 API 地址"
                showTestResult = true
                return
            }

            Task {
                var urlString = localAPIURL.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
                if urlString.hasSuffix("/v1") {
                    urlString += "/models"
                } else {
                    urlString += "/v1/models"
                }

                guard let url = URL(string: urlString) else {
                    testResultMessage = "无效的 API 地址"
                    showTestResult = true
                    return
                }

                var request = URLRequest(url: url)
                if !localAPIKey.isEmpty {
                    request.setValue("Bearer \(localAPIKey)", forHTTPHeaderField: "Authorization")
                }
                request.timeoutInterval = 10

                do {
                    let (data, _) = try await URLSession.shared.data(for: request)
                    if let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                       let models = json["data"] as? [[String: Any]] {
                        let modelNames = models.compactMap { $0["id"] as? String }
                        testResultMessage = "连接成功！可用模型: \(modelNames.joined(separator: ", "))"
                    } else {
                        testResultMessage = "连接成功，但无法解析模型列表"
                    }
                } catch {
                    testResultMessage = "连接失败: \(error.localizedDescription)"
                }
                showTestResult = true
            }
        }
    }

    private func handleImport(_ result: Result<URL, Error>) {
        switch result {
        case .success(let url):
            guard url.startAccessingSecurityScopedResource() else {
                statusMessage = "无法访问文件"
                return
            }
            defer { url.stopAccessingSecurityScopedResource() }

            do {
                let data = try Data(contentsOf: url)
                try DataService.importData(data, context: context)
                statusMessage = "数据导入成功"
            } catch {
                statusMessage = "导入失败: \(error.localizedDescription)"
            }
        case .failure(let error):
            statusMessage = "选择文件失败: \(error.localizedDescription)"
        }
    }
}

// MARK: - ShareSheet

struct ShareSheet: UIViewControllerRepresentable {
    let items: [Any]

    func makeUIViewController(context: Context) -> UIActivityViewController {
        UIActivityViewController(activityItems: items, applicationActivities: nil)
    }

    func updateUIViewController(_ uiViewController: UIActivityViewController, context: Context) {}
}

#Preview {
    SettingsView()
        .modelContainer(for: [AssetLayer.self, Holding.self, Snapshot.self, Transaction.self, Upload.self, ChecklistRecord.self, AppSetting.self], inMemory: true)
}
