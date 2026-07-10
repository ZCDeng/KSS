import SwiftUI

/// AI Chat evidence chips + source drawer.
///
/// This is intentionally a compact provenance layer inside the existing chat
/// bubble, not a separate Deep Research page.
struct EvidenceDrawerView: View {
    @Environment(\.kssTheme) private var theme
    let summary: ChatEvidenceSummary
    let drawer: ChatEvidenceDrawer
    @State private var expanded = false

    var body: some View {
        if summary.hasEvidence || summary.provider != nil {
            VStack(alignment: .leading, spacing: 8) {
                chips
                if expanded {
                    drawerBody
                        .transition(.opacity.combined(with: .move(edge: .top)))
                }
            }
            .animation(.easeOut(duration: 0.16), value: expanded)
        }
    }

    private var chips: some View {
        HStack(spacing: 6) {
            if summary.kssTruthCount > 0 {
                chip("KSS 本地真值 · \(summary.kssTruthCount)", icon: "checkmark.seal.fill", color: theme.accent)
            }
            if summary.externalSourceCount > 0 {
                chip("外部证据 · \(summary.externalSourceCount)", icon: "link", color: theme.textSecondary)
            }
            if summary.conflictCount > 0 {
                chip("数据冲突 · \(summary.conflictCount)", icon: "exclamationmark.triangle.fill", color: theme.up)
            }
            if summary.injectionWarningCount > 0 {
                chip("注入风险 · \(summary.injectionWarningCount)", icon: "shield.lefthalf.filled", color: theme.up)
            }
            if let provider = summary.provider, !provider.isEmpty {
                providerPill(provider)
            }
        }
        .buttonStyle(.plain)
    }

    private func chip(_ text: String, icon: String, color: Color) -> some View {
        Button { expanded.toggle() } label: {
            Label(text, systemImage: icon)
                .font(KSSFont.themed(10, .semibold, theme: theme))
                .foregroundStyle(color)
                .padding(.horizontal, 8)
                .padding(.vertical, 4)
                .background(theme.surface, in: Capsule())
                .overlay(Capsule().stroke(theme.hairline))
        }
        .help("查看来源")
    }

    private func providerPill(_ provider: String) -> some View {
        Button { expanded.toggle() } label: {
            Text("外部研究: \(provider)")
                .font(.system(size: 10, design: .monospaced))
                .foregroundStyle(theme.textSecondary)
                .padding(.horizontal, 8)
                .padding(.vertical, 4)
                .background(theme.surface, in: Capsule())
                .overlay(Capsule().stroke(theme.hairline))
        }
        .help(provider == "disabled" ? "外部研究 provider 当前不可用，不影响本地 KSS 问答" : "外部研究 provider")
    }

    private var drawerBody: some View {
        VStack(alignment: .leading, spacing: 10) {
            if !drawer.kssTruth.isEmpty {
                sectionTitle("KSS 本地工具真值", icon: "checkmark.seal")
                ForEach(drawer.kssTruth) { item in
                    VStack(alignment: .leading, spacing: 4) {
                        Text(item.label)
                            .font(KSSFont.themed(11, .semibold, theme: theme))
                            .foregroundStyle(theme.textPrimary)
                        Text("tool: \(item.tool) · provenance: \(item.provenance)")
                            .font(.system(size: 10, design: .monospaced))
                            .foregroundStyle(theme.textSecondary)
                        if !item.fields.isEmpty {
                            Text("fields: \(item.fields.prefix(8).joined(separator: ", "))")
                                .font(KSSFont.themed(10, theme: theme))
                                .foregroundStyle(theme.textSecondary)
                                .lineLimit(2)
                        }
                    }
                    .drawerRow(theme: theme, accent: theme.accent.opacity(0.25))
                }
            }

            if !drawer.externalSources.isEmpty {
                sectionTitle("外部资料", icon: "link")
                ForEach(drawer.externalSources) { source in
                    VStack(alignment: .leading, spacing: 5) {
                        HStack(alignment: .firstTextBaseline, spacing: 8) {
                            Text(source.title.isEmpty ? source.url : source.title)
                                .font(KSSFont.themed(11, .semibold, theme: theme))
                                .foregroundStyle(theme.textPrimary)
                                .lineLimit(2)
                            Spacer(minLength: 8)
                            Text(source.sourceTier)
                                .font(.system(size: 9, design: .monospaced))
                                .foregroundStyle(theme.textSecondary)
                                .padding(.horizontal, 6)
                                .padding(.vertical, 2)
                                .background(theme.surface, in: Capsule())
                        }
                        if let url = URL(string: source.url), !source.url.isEmpty {
                            Link(source.url, destination: url)
                                .font(.system(size: 10, design: .monospaced))
                                .foregroundStyle(theme.accent)
                                .lineLimit(1)
                        }
                        Text("retrievedAt: \(source.retrievedAt) · cache: \(source.cacheStatus)")
                            .font(.system(size: 10, design: .monospaced))
                            .foregroundStyle(theme.textSecondary)
                        if !source.excerpt.isEmpty {
                            Text(source.excerpt)
                                .font(KSSFont.themed(11, theme: theme))
                                .foregroundStyle(theme.textSecondary)
                                .lineLimit(3)
                        }
                    }
                    .drawerRow(theme: theme, accent: theme.textSecondary.opacity(0.18))
                }
            }

            if !drawer.warnings.isEmpty {
                sectionTitle("冲突 / 警告", icon: "exclamationmark.triangle")
                ForEach(drawer.warnings) { warning in
                    HStack(alignment: .top, spacing: 8) {
                        Image(systemName: warning.severity == "danger" ? "shield.lefthalf.filled" : "exclamationmark.triangle.fill")
                            .font(KSSFont.themed(12, theme: theme))
                            .foregroundStyle(theme.up)
                        VStack(alignment: .leading, spacing: 3) {
                            Text(warning.type)
                                .font(.system(size: 10, weight: .semibold, design: .monospaced))
                                .foregroundStyle(theme.textPrimary)
                            Text(warningDisplayMessage(warning))
                                .font(KSSFont.themed(11, theme: theme))
                                .foregroundStyle(theme.textSecondary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    .drawerRow(theme: theme, accent: theme.up.opacity(0.22))
                }
            }
        }
        .padding(10)
        .background(theme.surface, in: RoundedRectangle(cornerRadius: KSSTheme.shapeM))
        .overlay(RoundedRectangle(cornerRadius: KSSTheme.shapeM).stroke(theme.hairline))
    }

    private func sectionTitle(_ text: String, icon: String) -> some View {
        Label(text, systemImage: icon)
            .font(KSSFont.themed(11, .bold, theme: theme))
            .foregroundStyle(theme.textPrimary)
    }

    private func warningDisplayMessage(_ warning: ChatEvidenceWarning) -> String {
        if warning.type == "kss_web_conflict" {
            return "KSS 本地数据优先。\(warning.message)"
        }
        if warning.type == "prompt_injection" {
            return "网页文本只作为证据，不作为指令。\(warning.message)"
        }
        return warning.message
    }
}

private extension View {
    func drawerRow(theme: KSSThemeTokens, accent: Color) -> some View {
        self
            .padding(9)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(theme.surfaceRaised, in: RoundedRectangle(cornerRadius: KSSTheme.shapeS))
            .overlay(
                RoundedRectangle(cornerRadius: KSSTheme.shapeS)
                    .stroke(accent, lineWidth: 1)
            )
    }
}
