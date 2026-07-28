import 'package:flutter/material.dart';
import '../models/search_result.dart';
import '../models/chat_message.dart';

// ==================================================================== //
//  User query bubble — right-aligned, primary color
// ==================================================================== //

class UserBubble extends StatelessWidget {
  final String text;
  final String timeStr;

  const UserBubble({super.key, required this.text, required this.timeStr});

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 5),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.end,
        children: [
          Flexible(
            flex: 3,
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.end,
              children: [
                Container(
                  padding: const EdgeInsets.symmetric(
                    horizontal: 16,
                    vertical: 12,
                  ),
                  decoration: BoxDecoration(
                    color: theme.colorScheme.primary,
                    borderRadius: const BorderRadius.only(
                      topLeft: Radius.circular(20),
                      topRight: Radius.circular(20),
                      bottomLeft: Radius.circular(20),
                      bottomRight: Radius.circular(4),
                    ),
                  ),
                  child: Text(
                    text,
                    style: TextStyle(
                      color: theme.colorScheme.onPrimary,
                      fontSize: 15,
                      height: 1.4,
                    ),
                  ),
                ),
                const SizedBox(height: 2),
                Text(
                  timeStr,
                  style: theme.textTheme.labelSmall?.copyWith(
                    color: theme.colorScheme.outline,
                    fontSize: 11,
                  ),
                ),
              ],
            ),
          ),
          const SizedBox(width: 10),
          CircleAvatar(
            radius: 16,
            backgroundColor: theme.colorScheme.primaryContainer,
            child: Icon(
              Icons.person,
              size: 18,
              color: theme.colorScheme.onPrimaryContainer,
            ),
          ),
        ],
      ),
    );
  }
}

// ==================================================================== //
//  Server response group — left-aligned, shows all results + scores
// ==================================================================== //

class ResponseGroup extends StatelessWidget {
  final ChatExchange exchange;

  const ResponseGroup({super.key, required this.exchange});

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);

    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisAlignment: MainAxisAlignment.start,
        children: [
          // Avatar
          CircleAvatar(
            radius: 16,
            backgroundColor: theme.colorScheme.secondaryContainer,
            child: Icon(
              Icons.memory,
              size: 18,
              color: theme.colorScheme.onSecondaryContainer,
            ),
          ),
          const SizedBox(width: 10),
          Flexible(
            flex: 3,
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                // Status header
                _buildStatusHeader(context, theme),

                if (exchange.isLoading) ...[
                  const SizedBox(height: 12),
                  const _LoadingIndicator(),
                ] else if (exchange.isError) ...[
                  const SizedBox(height: 8),
                  _ErrorCard(error: exchange.error!, theme: theme),
                ] else if (exchange.isEmpty) ...[
                  const SizedBox(height: 8),
                  _EmptyCard(theme: theme),
                ] else ...[
                  const SizedBox(height: 4),
                  // Result count summary
                  Padding(
                    padding: const EdgeInsets.only(bottom: 6),
                    child: Text(
                      '${exchange.results.length} result${exchange.results.length == 1 ? '' : 's'} found',
                      style: theme.textTheme.bodySmall?.copyWith(
                        color: theme.colorScheme.outline,
                        fontSize: 12,
                      ),
                    ),
                  ),
                  // Result cards
                  ...exchange.results.asMap().entries.map(
                    (entry) => _ResponseResultCard(
                      result: entry.value,
                      index: entry.key,
                    ),
                  ),
                ],
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildStatusHeader(BuildContext context, ThemeData theme) {
    final timeStr =
        '${exchange.timestamp.hour.toString().padLeft(2, '0')}:${exchange.timestamp.minute.toString().padLeft(2, '0')}';

    String status;
    Color statusColor;
    if (exchange.isLoading) {
      status = 'Searching…';
      statusColor = theme.colorScheme.primary;
    } else if (exchange.isError) {
      status = 'Error';
      statusColor = theme.colorScheme.error;
    } else {
      status = 'Vector DB';
      statusColor = theme.colorScheme.secondary;
    }

    return Row(
      children: [
        Text(
          status,
          style: theme.textTheme.labelMedium?.copyWith(
            fontWeight: FontWeight.w700,
            color: statusColor,
          ),
        ),
        const SizedBox(width: 8),
        Text(
          timeStr,
          style: theme.textTheme.labelSmall?.copyWith(
            color: theme.colorScheme.outline,
            fontSize: 11,
          ),
        ),
      ],
    );
  }
}

// ==================================================================== //
//  Loading dots animation
// ==================================================================== //

class _LoadingIndicator extends StatefulWidget {
  const _LoadingIndicator();

  @override
  State<_LoadingIndicator> createState() => _LoadingIndicatorState();
}

class _LoadingIndicatorState extends State<_LoadingIndicator>
    with SingleTickerProviderStateMixin {
  late final AnimationController _ctrl;

  @override
  void initState() {
    super.initState();
    _ctrl = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1200),
    )..repeat();
  }

  @override
  void dispose() {
    _ctrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return AnimatedBuilder(
      animation: _ctrl,
      builder: (_, child) {
        return Container(
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 16),
          decoration: BoxDecoration(
            color: theme.colorScheme.surfaceContainerHighest.withOpacity(0.6),
            borderRadius: BorderRadius.circular(20),
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: List.generate(3, (i) {
              final delay = i * 0.2;
              final t = ((_ctrl.value - delay) % 1.0).clamp(0.0, 1.0);
              final opacity = 0.3 + 0.7 * (t < 0.5 ? t * 2 : 2 - t * 2);
              return Padding(
                padding: EdgeInsets.only(left: i == 0 ? 0 : 4),
                child: Container(
                  width: 8,
                  height: 8,
                  decoration: BoxDecoration(
                    shape: BoxShape.circle,
                    color: theme.colorScheme.primary.withOpacity(opacity),
                  ),
                ),
              );
            }),
          ),
        );
      },
    );
  }
}

// ==================================================================== //
//  Error card
// ==================================================================== //

class _ErrorCard extends StatelessWidget {
  final String error;
  final ThemeData theme;

  const _ErrorCard({required this.error, required this.theme});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: theme.colorScheme.errorContainer.withOpacity(0.5),
        borderRadius: BorderRadius.circular(16),
        border: Border.all(
          color: theme.colorScheme.error.withOpacity(0.3),
          width: 1,
        ),
      ),
      child: Row(
        children: [
          Icon(Icons.error_outline, color: theme.colorScheme.error, size: 20),
          const SizedBox(width: 10),
          Expanded(
            child: Text(
              'Connection failed.\n$error',
              style: TextStyle(
                color: theme.colorScheme.onErrorContainer,
                fontSize: 13,
              ),
            ),
          ),
        ],
      ),
    );
  }
}

// ==================================================================== //
//  Empty results card
// ==================================================================== //

class _EmptyCard extends StatelessWidget {
  final ThemeData theme;

  const _EmptyCard({required this.theme});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: theme.colorScheme.surfaceContainerHighest.withOpacity(0.5),
        borderRadius: BorderRadius.circular(16),
      ),
      child: Row(
        children: [
          Icon(Icons.search_off, color: theme.colorScheme.outline, size: 20),
          const SizedBox(width: 10),
          Text(
            'No matching documents found.',
            style: TextStyle(color: theme.colorScheme.outline, fontSize: 13),
          ),
        ],
      ),
    );
  }
}

// ==================================================================== //
//  Individual result card inside a response group
// ==================================================================== //

class _ResponseResultCard extends StatefulWidget {
  final SearchResult result;
  final int index;

  const _ResponseResultCard({required this.result, required this.index});

  @override
  State<_ResponseResultCard> createState() => _ResponseResultCardState();
}

class _ResponseResultCardState extends State<_ResponseResultCard> {
  bool _expanded = false;
  static const _previewLen = 150;

  Color _scoreColor(double similarity) {
    if (similarity >= 0.80) return Colors.green;
    if (similarity >= 0.65) return Colors.lightGreen;
    if (similarity >= 0.50) return Colors.orange;
    return Colors.redAccent;
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final r = widget.result;
    final scoreColor = _scoreColor(r.similarity);
    final tooLong = r.document.length > _previewLen;
    final displayText = (!tooLong || _expanded)
        ? r.document
        : '${r.document.substring(0, _previewLen)}…';

    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: Container(
        decoration: BoxDecoration(
          color: theme.colorScheme.surface,
          borderRadius: BorderRadius.circular(16),
          border: Border.all(
            color: theme.colorScheme.outlineVariant.withOpacity(0.4),
          ),
          boxShadow: [
            BoxShadow(
              color: Colors.black.withOpacity(0.04),
              blurRadius: 8,
              offset: const Offset(0, 2),
            ),
          ],
        ),
        clipBehavior: Clip.antiAlias,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // ── Header: rank + score badge ──────────────
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
              decoration: BoxDecoration(
                gradient: LinearGradient(
                  colors: [
                    scoreColor.withOpacity(0.08),
                    scoreColor.withOpacity(0.02),
                  ],
                  begin: Alignment.topLeft,
                  end: Alignment.bottomRight,
                ),
              ),
              child: Row(
                children: [
                  Container(
                    padding: const EdgeInsets.symmetric(
                      horizontal: 8,
                      vertical: 3,
                    ),
                    decoration: BoxDecoration(
                      color: theme.colorScheme.primaryContainer,
                      borderRadius: BorderRadius.circular(12),
                    ),
                    child: Text(
                      '#${widget.index + 1}',
                      style: theme.textTheme.labelSmall?.copyWith(
                        fontWeight: FontWeight.w700,
                        color: theme.colorScheme.onPrimaryContainer,
                      ),
                    ),
                  ),
                  const SizedBox(width: 10),
                  Expanded(
                    child: Text(
                      r.source,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: theme.textTheme.bodySmall?.copyWith(
                        color: theme.colorScheme.onSurfaceVariant,
                      ),
                    ),
                  ),
                  const SizedBox(width: 8),
                  // Score badge with progress bar
                  _ScoreBadge(
                    similarity: r.similarity,
                    percent: r.similarityPercent,
                    color: scoreColor,
                  ),
                ],
              ),
            ),

            // ── Body: document preview ─────────────────
            Padding(
              padding: const EdgeInsets.fromLTRB(14, 10, 14, 4),
              child: Text(
                displayText,
                style: theme.textTheme.bodyMedium?.copyWith(
                  height: 1.55,
                  fontSize: 14,
                ),
              ),
            ),
            if (tooLong)
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 14),
                child: GestureDetector(
                  onTap: () => setState(() => _expanded = !_expanded),
                  child: Text(
                    _expanded ? '▲ Show less' : '▼ Show more',
                    style: TextStyle(
                      color: theme.colorScheme.primary,
                      fontWeight: FontWeight.w600,
                      fontSize: 12,
                    ),
                  ),
                ),
              ),

            // ── Footer: metadata + distance ────────────
            Padding(
              padding: const EdgeInsets.fromLTRB(14, 8, 14, 12),
              child: Wrap(
                spacing: 6,
                runSpacing: 6,
                children: [
                  _MiniChip(
                    icon: Icons.description,
                    label: r.docType,
                    theme: theme,
                  ),
                  if (r.pageInfo.isNotEmpty)
                    _MiniChip(
                      icon: Icons.bookmark,
                      label: r.pageInfo,
                      theme: theme,
                    ),
                  _MiniChip(
                    icon: Icons.straighten,
                    label: 'dist ${r.distance.toStringAsFixed(4)}',
                    theme: theme,
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

// ==================================================================== //
//  Score badge with mini progress bar
// ==================================================================== //

class _ScoreBadge extends StatelessWidget {
  final double similarity;
  final String percent;
  final Color color;

  const _ScoreBadge({
    required this.similarity,
    required this.percent,
    required this.color,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
      decoration: BoxDecoration(
        color: color.withOpacity(0.12),
        borderRadius: BorderRadius.circular(20),
        border: Border.all(color: color.withOpacity(0.5), width: 1),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(Icons.auto_awesome, size: 13, color: color),
          const SizedBox(width: 5),
          Text(
            percent,
            style: TextStyle(
              fontWeight: FontWeight.w800,
              color: color,
              fontSize: 12,
            ),
          ),
          const SizedBox(width: 6),
          SizedBox(
            width: 36,
            height: 4,
            child: ClipRRect(
              borderRadius: BorderRadius.circular(2),
              child: LinearProgressIndicator(
                value: similarity,
                backgroundColor: color.withOpacity(0.15),
                valueColor: AlwaysStoppedAnimation(color),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

// ==================================================================== //
//  Mini metadata chip
// ==================================================================== //

class _MiniChip extends StatelessWidget {
  final IconData icon;
  final String label;
  final ThemeData theme;

  const _MiniChip({
    required this.icon,
    required this.label,
    required this.theme,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 3),
      decoration: BoxDecoration(
        color: theme.colorScheme.surfaceContainerHighest.withOpacity(0.7),
        borderRadius: BorderRadius.circular(6),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(icon, size: 11, color: theme.colorScheme.onSurfaceVariant),
          const SizedBox(width: 3),
          Text(
            label,
            style: theme.textTheme.labelSmall?.copyWith(
              color: theme.colorScheme.onSurfaceVariant,
              fontSize: 10,
            ),
          ),
        ],
      ),
    );
  }
}
