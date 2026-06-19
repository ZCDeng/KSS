import SwiftUI
import AppKit
import Vision
import UniformTypeIdentifiers

/// 本地 OCR：用 Apple Vision 框架（端上、随系统、支持中文）识别图片里的股票名称/代码。
enum StockOCR {
    static func recognize(_ image: NSImage) async -> String {
        guard let cg = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else { return "" }
        return await withCheckedContinuation { cont in
            let request = VNRecognizeTextRequest { req, _ in
                let lines = (req.results as? [VNRecognizedTextObservation] ?? [])
                    .compactMap { $0.topCandidates(1).first?.string }
                cont.resume(returning: lines.joined(separator: "\n"))
            }
            request.recognitionLevel = .accurate
            request.recognitionLanguages = ["zh-Hans", "en-US"]
            request.usesLanguageCorrection = false
            let handler = VNImageRequestHandler(cgImage: cg, options: [:])
            do { try handler.perform([request]) }
            catch { cont.resume(returning: "") }
        }
    }
}

/// 股票池导入：手工输入（名称/代码）+ 图片 OCR 识别 → 解析为 ts_code → 导入并同步日线。
struct ImportStocksView: View {
    @EnvironmentObject private var store: KSSStore
    var onClose: () -> Void

    @State private var text = ""
    @State private var resolved: [ResolvedStock] = []
    @State private var resolving = false
    @State private var ocrBusy = false
    @State private var importing = false

    private var importable: [ResolvedStock] { resolved.filter { $0.ok && !$0.inPool } }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .firstTextBaseline) {
                VStack(alignment: .leading, spacing: 3) {
                    Text("导入股票")
                        .font(KSSFont.serif(22, .bold))
                        .foregroundStyle(KSSTheme.textPrimary)
                    Text("手工输入名称/代码，或图片识别导入 · 识别后拉取日线进入股票池")
                        .font(.system(size: 12))
                        .foregroundStyle(KSSTheme.textSecondary)
                }
                Spacer()
                Button { onClose() } label: { Label("关闭", systemImage: "xmark").font(.system(size: 13, weight: .semibold)) }
                    .buttonStyle(.bordered)
                    .keyboardShortcut(.cancelAction)
            }

            // 输入区（支持拖拽图片）
            ZStack(alignment: .topLeading) {
                TextEditor(text: $text)
                    .font(.system(size: 13, design: .monospaced))
                    .scrollContentBackground(.hidden)
                    .padding(8)
                    .frame(height: 110)
                    .background(KSSTheme.surface, in: RoundedRectangle(cornerRadius: 8))
                    .overlay(RoundedRectangle(cornerRadius: 8).stroke(KSSTheme.hairline))
                if text.isEmpty {
                    Text("例：贵州茅台  600519  平安银行  000001.SZ … 或把截图拖进来")
                        .font(.system(size: 12.5))
                        .foregroundStyle(KSSTheme.textSecondary)
                        .padding(.horizontal, 14).padding(.vertical, 14)
                        .allowsHitTesting(false)
                }
            }
            .onDrop(of: [.image, .fileURL], isTargeted: nil) { providers in
                handleDrop(providers); return true
            }

            HStack(spacing: 10) {
                Button { pickImageAndOCR() } label: {
                    Label(ocrBusy ? "识别中…" : "识别图片导入", systemImage: "text.viewfinder")
                        .font(.system(size: 13, weight: .semibold))
                }
                .buttonStyle(.bordered)
                .disabled(ocrBusy)

                Button { resolve() } label: {
                    Label(resolving ? "解析中…" : "解析", systemImage: "wand.and.stars")
                        .font(.system(size: 13, weight: .semibold))
                }
                .buttonStyle(.borderedProminent)
                .tint(KSSTheme.accent)
                .disabled(text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || resolving)

                Spacer()
            }

            if !resolved.isEmpty {
                Divider().overlay(KSSTheme.hairline)
                ScrollView {
                    VStack(spacing: 4) {
                        ForEach(resolved) { row($0) }
                    }
                }
                .frame(maxHeight: .infinity)

                HStack {
                    Text("可导入 \(importable.count) 只 · 已识别 \(resolved.filter{ $0.ok }.count)/\(resolved.count)")
                        .font(.system(size: 12.5))
                        .foregroundStyle(KSSTheme.textSecondary)
                    Spacer()
                    Button {
                        Task {
                            importing = true
                            await store.importStocks(importable.map { $0.code })
                            importing = false
                            onClose()
                        }
                    } label: {
                        Label(importing ? "导入同步中…" : "导入并同步 \(importable.count) 只", systemImage: "square.and.arrow.down")
                            .font(.system(size: 13, weight: .semibold))
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(KSSTheme.accent)
                    .disabled(importable.isEmpty || importing)
                }
            } else {
                Spacer()
            }
        }
        .padding(20)
        .frame(width: 580, height: 600)
        .background(KSSTheme.canvas)
    }

    private func row(_ item: ResolvedStock) -> some View {
        HStack(spacing: 10) {
            Image(systemName: item.ok ? (item.inPool ? "checkmark.circle" : "checkmark.circle.fill") : "questionmark.circle")
                .foregroundStyle(item.ok ? (item.inPool ? KSSTheme.textSecondary : KSSTheme.down) : KSSTheme.up)
                .font(.system(size: 14))
            Text(item.query)
                .font(.system(size: 13, weight: .medium))
                .foregroundStyle(KSSTheme.textBody)
                .frame(width: 120, alignment: .leading)
                .lineLimit(1)
            if item.ok {
                Text(item.name)
                    .font(.system(size: 13, weight: .bold))
                    .foregroundStyle(KSSTheme.textPrimary)
                    .lineLimit(1)
                Text(item.code)
                    .font(.system(size: 11.5, design: .monospaced))
                    .foregroundStyle(KSSTheme.textSecondary)
            } else {
                Text("未匹配")
                    .font(.system(size: 12))
                    .foregroundStyle(KSSTheme.up)
            }
            Spacer()
            if item.inPool {
                Text("已在池")
                    .font(.system(size: 10.5, weight: .semibold))
                    .foregroundStyle(KSSTheme.textSecondary)
                    .padding(.horizontal, 7).padding(.vertical, 2)
                    .background(KSSTheme.textSecondary.opacity(0.15), in: Capsule())
            }
        }
        .padding(.horizontal, 10).padding(.vertical, 7)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(KSSTheme.surface, in: RoundedRectangle(cornerRadius: 7))
    }

    // MARK: 动作

    private func resolve() {
        let query = text
        resolving = true
        Task {
            let r = await store.resolveStocks(query)
            resolved = r
            resolving = false
        }
    }

    private func pickImageAndOCR() {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.image, .png, .jpeg, .tiff, .heic]
        panel.allowsMultipleSelection = false
        guard panel.runModal() == .OK, let url = panel.url, let image = NSImage(contentsOf: url) else { return }
        runOCR(image)
    }

    private func handleDrop(_ providers: [NSItemProvider]) {
        guard let provider = providers.first else { return }
        _ = provider.loadObject(ofClass: NSImage.self) { obj, _ in
            if let image = obj as? NSImage {
                DispatchQueue.main.async { runOCR(image) }
            }
        }
    }

    private func runOCR(_ image: NSImage) {
        ocrBusy = true
        Task {
            let recognized = await StockOCR.recognize(image)
            await MainActor.run {
                text = text.isEmpty ? recognized : text + "\n" + recognized
                ocrBusy = false
            }
            resolve()
        }
    }
}
