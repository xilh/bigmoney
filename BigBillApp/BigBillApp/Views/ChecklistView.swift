import SwiftUI
import SwiftData

struct ChecklistView: View {
    @Environment(\.modelContext) private var context
    @Query(sort: \ChecklistRecord.completedAt, order: .reverse) private var records: [ChecklistRecord]

    @State private var selectedPeriod: PeriodType = .weekly
    @State private var checkedItems: Set<String> = []
    @State private var notes = ""

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 16) {
                    // Period selector
                    Picker("检视类型", selection: $selectedPeriod) {
                        ForEach(PeriodType.allCases, id: \.self) { period in
                            Text(period.displayName).tag(period)
                        }
                    }
                    .pickerStyle(.segmented)
                    .onChange(of: selectedPeriod) { _, _ in
                        checkedItems.removeAll()
                        notes = ""
                    }

                    // Period info
                    HStack {
                        Image(systemName: "clock")
                            .foregroundStyle(.secondary)
                        Text(selectedPeriod.periodDescription)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                        Spacer()
                    }

                    // Last completion
                    if let lastRecord = records.first(where: { $0.periodType == selectedPeriod }) {
                        HStack {
                            Image(systemName: "checkmark.circle")
                                .foregroundStyle(.green)
                            Text("上次完成: \(lastRecord.completedAt.fullString)")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                            Spacer()
                        }
                        .padding(8)
                        .background(Color.green.opacity(0.08))
                        .clipShape(RoundedRectangle(cornerRadius: 8))
                    }

                    // Checklist items
                    VStack(alignment: .leading, spacing: 8) {
                        ForEach(selectedPeriod.items, id: \.self) { item in
                            Button {
                                if checkedItems.contains(item) {
                                    checkedItems.remove(item)
                                } else {
                                    checkedItems.insert(item)
                                }
                            } label: {
                                HStack(alignment: .top, spacing: 10) {
                                    Image(systemName: checkedItems.contains(item) ? "checkmark.square.fill" : "square")
                                        .foregroundStyle(checkedItems.contains(item) ? .green : .secondary)
                                        .font(.title3)

                                    Text(item)
                                        .font(.subheadline)
                                        .foregroundStyle(.primary)
                                        .multilineTextAlignment(.leading)

                                    Spacer()
                                }
                            }
                            .padding(10)
                            .background(
                                checkedItems.contains(item) ?
                                Color.green.opacity(0.05) : Color(.secondarySystemGroupedBackground)
                            )
                            .clipShape(RoundedRectangle(cornerRadius: 8))
                        }
                    }

                    // Notes
                    VStack(alignment: .leading, spacing: 8) {
                        Text("备注")
                            .font(.subheadline)
                            .fontWeight(.medium)
                        TextEditor(text: $notes)
                            .frame(minHeight: 60)
                            .padding(8)
                            .background(Color(.secondarySystemGroupedBackground))
                            .clipShape(RoundedRectangle(cornerRadius: 8))
                    }

                    // Progress & Save
                    VStack(spacing: 12) {
                        let total = selectedPeriod.items.count
                        let completed = checkedItems.count

                        ProgressView(value: Double(completed), total: Double(total))
                            .tint(.green)

                        Text("\(completed)/\(total) 已完成")
                            .font(.caption)
                            .foregroundStyle(.secondary)

                        Button {
                            saveChecklist()
                        } label: {
                            Text("保存检视记录")
                                .frame(maxWidth: .infinity)
                                .padding()
                                .background(completed > 0 ? .blue : .gray)
                                .foregroundStyle(.white)
                                .clipShape(RoundedRectangle(cornerRadius: 10))
                        }
                        .disabled(completed == 0)
                    }
                }
                .padding()
            }
            .background(Color(.systemGroupedBackground))
            .navigationTitle("检视清单")
        }
    }

    private func saveChecklist() {
        let record = ChecklistRecord(
            periodType: selectedPeriod,
            completedItems: Array(checkedItems),
            totalItems: selectedPeriod.items.count,
            notes: notes
        )
        context.insert(record)
        try? context.save()

        // Reset
        checkedItems.removeAll()
        notes = ""
    }
}

#Preview {
    ChecklistView()
        .modelContainer(for: [AssetLayer.self, Holding.self, Snapshot.self, Transaction.self, Upload.self, ChecklistRecord.self, AppSetting.self], inMemory: true)
}
