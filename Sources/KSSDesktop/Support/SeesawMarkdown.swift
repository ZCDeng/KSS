import Foundation
import SwiftUI

/// The small, deterministic Markdown subset emitted by Seesaw's providers.
/// Keeping it native avoids a nested web view in the streaming transcript while
/// still making headings, lists and market-data tables readable as they arrive.
enum SeesawMarkdownBlock: Equatable {
    case heading(level: Int, text: String)
    case paragraph(String)
    case list(ordered: Bool, items: [String])
    case table(headers: [String], rows: [[String]])
    case quote(String)
    case code(String)
    case divider
}

enum SeesawTableColumn: Equatable {
    case text
    case number
    case signedChange
}

enum SeesawMarkdownLayout {
    /// DESIGN.md：Seesaw 正文 15pt，走 Chirp / HarmonyOS（`KSSFont.themed`）。
    static let bodyFontSize: CGFloat = 15
    static let tableFontSize: CGFloat = 12
    static let tableHorizontalPadding: CGFloat = 8
    static func headingSize(for level: Int) -> CGFloat {
        switch level {
        case 1: return 19
        case 2: return 16.5
        case 3: return 15
        default: return 14.5
        }
    }

    /// Transcript stays native-only so height tracks content (no nested WebView min-height bloat).
    /// Long-form report pages keep using `MarkdownWebView` directly.

    static func tableColumnWidth(columnCount: Int) -> CGFloat {
        switch max(1, columnCount) {
        case 1: return 320
        case 2: return 220
        case 3: return 170
        case 4: return 145
        case 5: return 128
        case 6: return 112
        default: return 106
        }
    }

    static func tableContentWidth(columnCount: Int) -> CGFloat {
        tableColumnWidth(columnCount: columnCount) * CGFloat(max(1, columnCount))
    }

    /// 数值列：该列多数非空单元格以数字或正负号开头。表头含涨跌/涨幅/收益时再套红涨绿跌。
    static func columnKinds(headers: [String], rows: [[String]]) -> [SeesawTableColumn] {
        headers.indices.map { index in
            let samples = rows.compactMap { row -> String? in
                guard index < row.count else { return nil }
                let plain = plainCell(row[index]).trimmingCharacters(in: .whitespacesAndNewlines)
                if plain.isEmpty || plain == "-" || plain == "—" || plain == "–" { return nil }
                return plain
            }
            let numericCount = samples.filter(isNumericCell).count
            let numeric = !samples.isEmpty && numericCount * 2 >= samples.count
            let header = headers[index]
            let signed = ["涨跌", "涨幅", "收益"].contains { header.contains($0) }
            if signed && numeric { return .signedChange }
            return numeric ? .number : .text
        }
    }

    /// 单元格里第一个带符号的数：正为 1，负为 -1，没有符号为 0。
    static func signedDirection(_ raw: String) -> Int {
        let plain = plainCell(raw)
        guard let expression = try? NSRegularExpression(pattern: "[+＋\\-−–]\\s*\\d") else { return 0 }
        let range = NSRange(plain.startIndex..., in: plain)
        guard let match = expression.firstMatch(in: plain, range: range),
              let hit = Range(match.range, in: plain) else { return 0 }
        let token = plain[hit].trimmingCharacters(in: .whitespaces)
        if token.hasPrefix("-") || token.hasPrefix("−") || token.hasPrefix("–") { return -1 }
        if token.hasPrefix("+") || token.hasPrefix("＋") { return 1 }
        return 0
    }

    static func plainCell(_ raw: String) -> String {
        raw.replacingOccurrences(of: "**", with: "")
            .replacingOccurrences(of: "*", with: "")
            .replacingOccurrences(of: "`", with: "")
    }

    static func isNumericCell(_ raw: String) -> Bool {
        let text = plainCell(raw).trimmingCharacters(in: .whitespacesAndNewlines)
        guard let first = text.unicodeScalars.first else { return false }
        let starters = CharacterSet(charactersIn: "+＋-−–0123456789")
        guard starters.contains(first) else { return false }
        return text.unicodeScalars.contains { CharacterSet.decimalDigits.contains($0) }
    }
}

enum SeesawMarkdown {
    static func parse(_ markdown: String) -> [SeesawMarkdownBlock] {
        let lines = markdown
            .replacingOccurrences(of: "\r\n", with: "\n")
            .replacingOccurrences(of: "\r", with: "\n")
            .components(separatedBy: "\n")

        var blocks: [SeesawMarkdownBlock] = []
        var paragraph: [String] = []
        var index = 0

        func flushParagraph() {
            let text = paragraph.joined(separator: "\n").trimmingCharacters(in: .whitespacesAndNewlines)
            if !text.isEmpty {
                blocks.append(.paragraph(text))
            }
            paragraph.removeAll(keepingCapacity: true)
        }

        while index < lines.count {
            let line = lines[index]
            let trimmed = line.trimmingCharacters(in: .whitespaces)

            guard !trimmed.isEmpty else {
                flushParagraph()
                index += 1
                continue
            }

            if let heading = heading(in: trimmed) {
                flushParagraph()
                blocks.append(.heading(level: heading.level, text: heading.text))
                index += 1
                continue
            }

            if isDivider(trimmed) {
                flushParagraph()
                blocks.append(.divider)
                index += 1
                continue
            }

            if trimmed.hasPrefix("```") {
                flushParagraph()
                index += 1
                var codeLines: [String] = []
                while index < lines.count, !lines[index].trimmingCharacters(in: .whitespaces).hasPrefix("```") {
                    codeLines.append(lines[index])
                    index += 1
                }
                if index < lines.count { index += 1 }
                blocks.append(.code(codeLines.joined(separator: "\n")))
                continue
            }

            if index + 1 < lines.count,
               let headers = tableCells(in: trimmed),
               isTableSeparator(lines[index + 1]) {
                flushParagraph()
                index += 2
                var rows: [[String]] = []
                while index < lines.count,
                      let cells = tableCells(in: lines[index]),
                      cells.count == headers.count {
                    rows.append(cells)
                    index += 1
                }
                blocks.append(.table(headers: headers, rows: rows))
                continue
            }

            if let item = listItem(in: trimmed) {
                flushParagraph()
                let ordered = item.ordered
                var items = [item.text]
                index += 1
                while index < lines.count,
                      let next = listItem(in: lines[index].trimmingCharacters(in: .whitespaces)),
                      next.ordered == ordered {
                    items.append(next.text)
                    index += 1
                }
                blocks.append(.list(ordered: ordered, items: items))
                continue
            }

            if trimmed.hasPrefix(">") {
                flushParagraph()
                blocks.append(.quote(String(trimmed.dropFirst()).trimmingCharacters(in: .whitespaces)))
                index += 1
                continue
            }

            paragraph.append(trimmed)
            index += 1
        }

        flushParagraph()
        return blocks
    }

    /// Incomplete streamed markup (`**bold` / unmatched ticks) must not go through
    /// AttributedString — Apple's parser can swallow the rest of the paragraph.
    static func inlineMarkupIsBalanced(_ text: String) -> Bool {
        let boldMarks = text.components(separatedBy: "**").count - 1
        guard boldMarks % 2 == 0 else { return false }
        return text.filter { $0 == "`" }.count % 2 == 0
    }

    private static func heading(in line: String) -> (level: Int, text: String)? {
        var level = 0
        for character in line {
            guard character == "#" else { break }
            level += 1
        }
        guard (1...6).contains(level) else { return nil }
        let remainder = String(line.dropFirst(level))
        guard remainder.first?.isWhitespace == true else { return nil }
        let text = remainder.trimmingCharacters(in: .whitespaces)
        return text.isEmpty ? nil : (level, text)
    }

    private static func isDivider(_ line: String) -> Bool {
        let normalized = line.replacingOccurrences(of: " ", with: "")
        return normalized == "---" || normalized == "***" || normalized == "___"
    }

    private static func tableCells(in line: String) -> [String]? {
        let trimmed = line.trimmingCharacters(in: .whitespaces)
        guard trimmed.contains("|") else { return nil }
        var body = trimmed
        if body.hasPrefix("|") { body.removeFirst() }
        if body.hasSuffix("|") { body.removeLast() }
        let cells = body.split(separator: "|", omittingEmptySubsequences: false)
            .map { String($0).trimmingCharacters(in: .whitespaces) }
        return cells.count >= 2 && cells.allSatisfy { !$0.isEmpty } ? cells : nil
    }

    private static func isTableSeparator(_ line: String) -> Bool {
        guard let cells = tableCells(in: line) else { return false }
        return cells.allSatisfy { cell in
            cell.range(of: "^:?-{3,}:?$", options: .regularExpression) != nil
        }
    }

    private static func listItem(in line: String) -> (ordered: Bool, text: String)? {
        for marker in ["- ", "* ", "+ "] where line.hasPrefix(marker) {
            let text = String(line.dropFirst(marker.count)).trimmingCharacters(in: .whitespaces)
            return text.isEmpty ? nil : (false, text)
        }
        let pattern = "^[0-9]+\\.\\s+(.+)$"
        guard let expression = try? NSRegularExpression(pattern: pattern) else { return nil }
        let range = NSRange(line.startIndex..., in: line)
        guard let match = expression.firstMatch(in: line, range: range), match.numberOfRanges > 1,
              let textRange = Range(match.range(at: 1), in: line) else { return nil }
        let text = String(line[textRange]).trimmingCharacters(in: .whitespaces)
        return text.isEmpty ? nil : (true, text)
    }
}

struct SeesawMarkdownView: View {
    @Environment(\.kssTheme) private var theme

    let markdown: String
    let errorTint: Color?

    init(markdown: String, errorTint: Color? = nil) {
        self.markdown = markdown
        self.errorTint = errorTint
    }

    var body: some View {
        // Always native in transcript: height == content (WebView fitsContent
        // inflated short replies into tall empty frames).
        VStack(alignment: .leading, spacing: 6) {
            if errorTint != nil {
                Text("生成异常")
                    .font(KSSFont.themed(11, .semibold, theme: theme))
                    .foregroundStyle(errorTint ?? theme.down)
            }
            VStack(alignment: .leading, spacing: 7) {
                ForEach(Array(SeesawMarkdown.parse(markdown).enumerated()), id: \.offset) { _, block in
                    blockView(block)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .fixedSize(horizontal: false, vertical: true)
            .opacity(errorTint == nil ? 1 : 0.92)
            .textSelection(.enabled)
        }
        .fixedSize(horizontal: false, vertical: true)
    }

    @ViewBuilder
    private func blockView(_ block: SeesawMarkdownBlock) -> some View {
        switch block {
        case let .heading(level, text):
            inlineText(text)
                .font(KSSFont.themed(SeesawMarkdownLayout.headingSize(for: level), .bold, theme: theme))
                .foregroundStyle(level <= 2 ? theme.textPrimary : foreground)
                .padding(.top, level <= 2 ? 4 : 2)
        case let .paragraph(text):
            inlineText(text)
                .font(KSSFont.themed(SeesawMarkdownLayout.bodyFontSize, theme: theme))
                .foregroundStyle(foreground)
                .lineSpacing(2.5)
                .fixedSize(horizontal: false, vertical: true)
        case let .list(ordered, items):
            VStack(alignment: .leading, spacing: 4) {
                ForEach(Array(items.enumerated()), id: \.offset) { index, item in
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        Text(ordered ? "\(index + 1)." : "•")
                            .font(KSSFont.themed(12.5, .semibold, theme: theme))
                            .foregroundStyle(theme.accent.opacity(0.85))
                            .frame(width: ordered ? 20 : 12, alignment: .trailing)
                        inlineText(item)
                            .font(KSSFont.themed(SeesawMarkdownLayout.bodyFontSize, theme: theme))
                            .foregroundStyle(foreground)
                            .lineSpacing(2)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
        case let .table(headers, rows):
            table(headers: headers, rows: rows)
        case let .quote(text):
            HStack(spacing: 10) {
                Rectangle()
                    .fill(theme.accent.opacity(0.7))
                    .frame(width: 3)
                inlineText(text)
                    .font(KSSFont.themed(13.5, .medium, theme: theme))
                    .foregroundStyle(theme.textSecondary)
                    .lineSpacing(2)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .padding(.vertical, 3)
        case let .code(text):
            Text(text)
                .font(.system(size: 11.5, design: .monospaced))
                .foregroundStyle(foreground)
                .textSelection(.enabled)
                .padding(10)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(theme.surfaceContainer, in: RoundedRectangle(cornerRadius: 8))
        case .divider:
            Rectangle()
                .fill(theme.hairline)
                .frame(height: 1)
                .padding(.vertical, 3)
        }
    }

    private func table(headers: [String], rows: [[String]]) -> some View {
        let kinds = SeesawMarkdownLayout.columnKinds(headers: headers, rows: rows)
        return ScrollView(.horizontal, showsIndicators: true) {
            VStack(spacing: 0) {
                tableRow(headers, isHeader: true, kinds: kinds)
                ForEach(Array(rows.enumerated()), id: \.offset) { _, row in
                    tableRow(row, isHeader: false, kinds: kinds)
                }
            }
            .fixedSize(horizontal: true, vertical: true)
            .background(theme.surfaceContainer, in: RoundedRectangle(cornerRadius: 8))
            .overlay {
                RoundedRectangle(cornerRadius: 8)
                    .stroke(theme.hairline)
            }
        }
        .fixedSize(horizontal: false, vertical: true)
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func tableRow(_ cells: [String], isHeader: Bool, kinds: [SeesawTableColumn]) -> some View {
        let columnWidth = SeesawMarkdownLayout.tableColumnWidth(columnCount: cells.count)
        let contentWidth = max(
            72,
            columnWidth - SeesawMarkdownLayout.tableHorizontalPadding * 2
        )
        return HStack(spacing: 0) {
            ForEach(cells.indices, id: \.self) { index in
                let kind = index < kinds.count ? kinds[index] : .text
                let align: Alignment = kind == .text ? .leading : .trailing
                inlineText(cells[index])
                    .font(tableFont(kind: kind, isHeader: isHeader))
                    .multilineTextAlignment(kind == .text ? .leading : .trailing)
                    .foregroundStyle(cellColor(cells[index], kind: kind, isHeader: isHeader))
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(width: contentWidth, alignment: align)
                    .padding(.horizontal, SeesawMarkdownLayout.tableHorizontalPadding)
                    .padding(.vertical, 7)
                    .overlay(alignment: .trailing) {
                        if index < cells.count - 1 {
                            Rectangle().fill(theme.hairline).frame(width: 1)
                        }
                    }
            }
        }
        .background(isHeader ? theme.accentSoft.opacity(0.45) : Color.clear)
        .overlay(alignment: .bottom) {
            Rectangle().fill(theme.hairline).frame(height: 1)
        }
    }

    private func tableFont(kind: SeesawTableColumn, isHeader: Bool) -> Font {
        let base = KSSFont.themed(
            SeesawMarkdownLayout.tableFontSize,
            isHeader ? .semibold : .regular,
            theme: theme
        )
        return kind == .text ? base : base.monospacedDigit()
    }

    private func cellColor(_ text: String, kind: SeesawTableColumn, isHeader: Bool) -> Color {
        guard !isHeader, kind == .signedChange else {
            return isHeader ? theme.textPrimary : foreground
        }
        switch SeesawMarkdownLayout.signedDirection(text) {
        case 1: return theme.up
        case -1: return theme.down
        default: return foreground
        }
    }

    private var foreground: Color { errorTint ?? theme.textPrimary }

    private func inlineText(_ text: String) -> Text {
        if SeesawMarkdown.inlineMarkupIsBalanced(text),
           let attributed = try? AttributedString(
            markdown: text,
            options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)
        ) {
            return Text(attributed)
        }
        return Text(text)
    }
}
