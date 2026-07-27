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

enum SeesawMarkdownLayout {
    static let bodyFontSize: CGFloat = 14
    static let tableFontSize: CGFloat = 11.5
    static let tableHorizontalPadding: CGFloat = 8

    static func headingSize(for level: Int) -> CGFloat {
        switch level {
        case 1: return 20
        case 2: return 17.5
        case 3: return 15.5
        default: return 14.5
        }
    }

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
        let blocks = SeesawMarkdown.parse(markdown)
        LazyVStack(alignment: .leading, spacing: 9) {
            ForEach(Array(blocks.enumerated()), id: \.offset) { _, block in
                blockView(block)
            }
        }
        .textSelection(.enabled)
    }

    @ViewBuilder
    private func blockView(_ block: SeesawMarkdownBlock) -> some View {
        switch block {
        case let .heading(level, text):
            inlineText(text)
                .font(KSSFont.themed(SeesawMarkdownLayout.headingSize(for: level), .bold, theme: theme))
                .foregroundStyle(foreground)
                .padding(.top, level <= 2 ? 7 : 3)
        case let .paragraph(text):
            inlineText(text)
                .font(KSSFont.themed(SeesawMarkdownLayout.bodyFontSize, theme: theme))
                .foregroundStyle(foreground)
                .lineSpacing(3)
                .fixedSize(horizontal: false, vertical: true)
        case let .list(ordered, items):
            VStack(alignment: .leading, spacing: 5) {
                ForEach(Array(items.enumerated()), id: \.offset) { index, item in
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        Text(ordered ? "\(index + 1)." : "•")
                            .font(KSSFont.themed(13, .semibold, theme: theme))
                            .foregroundStyle(theme.textSecondary)
                            .frame(width: ordered ? 22 : 12, alignment: .trailing)
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
                    .font(KSSFont.themed(13.5, theme: theme))
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
        ScrollView(.horizontal, showsIndicators: true) {
            VStack(spacing: 0) {
                tableRow(headers, isHeader: true)
                ForEach(Array(rows.enumerated()), id: \.offset) { _, row in
                    tableRow(row, isHeader: false)
                }
            }
            .background(theme.surfaceContainer, in: RoundedRectangle(cornerRadius: 8))
            .overlay {
                RoundedRectangle(cornerRadius: 8)
                    .stroke(theme.hairline)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func tableRow(_ cells: [String], isHeader: Bool) -> some View {
        let columnWidth = SeesawMarkdownLayout.tableColumnWidth(columnCount: cells.count)
        let contentWidth = max(
            72,
            columnWidth - SeesawMarkdownLayout.tableHorizontalPadding * 2
        )
        return HStack(spacing: 0) {
            ForEach(cells.indices, id: \.self) { index in
                inlineText(cells[index])
                    .font(
                        KSSFont.themed(
                            SeesawMarkdownLayout.tableFontSize,
                            isHeader ? .semibold : .regular,
                            theme: theme
                        )
                    )
                    .foregroundStyle(isHeader ? theme.textPrimary : foreground)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(width: contentWidth, alignment: .leading)
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

    private var foreground: Color { errorTint ?? theme.textPrimary }

    private func inlineText(_ text: String) -> Text {
        if let attributed = try? AttributedString(
            markdown: text,
            options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)
        ) {
            return Text(attributed)
        }
        return Text(text)
    }
}
