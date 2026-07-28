import 'package:flutter/material.dart';
import '../models/search_result.dart';

/// A card that displays a single similarity-search result with
/// document preview, metadata chips, and a color-coded similarity badge.
class ResultCard extends StatelessWidget {
  final SearchResult result;
  final int index;

  const ResultCard({super.key, required this.result, required this.index});

  /// Map similarity [0,1] → color.
  Color _scoreColor(double similarity) {
    if (similarity >= 0.80) return Colors.green;
    if (similarity >= 0.65) return Colors.lightGreen;
    if (similarity >= 0.50) return Colors.orange;
    return Colors.redAccent;
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);

    return Card(
      margin: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
      elevation: 2,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // ── Header: rank + similarity badge ─────────────────
            Row(
              children: [
                Container(
                  padding:
                      const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
                  decoration: BoxDecoration(
                    color: theme.colorScheme.primaryContainer,
                    borderRadius: BorderRadius.circular(20),
                  ),
                  child: Text(
                    '#${index + 1}',
                    style: theme.textTheme.labelMedium?.copyWith(
                      fontWeight: FontWeight.bold,
                      color: theme.colorScheme.onPrimaryContainer,
                    ),
                  ),
                ),
                const Spacer(),
                _ScoreBadge(
                  similarity: result.similarity,
                  percent: result.similarityPercent,
                  color: _scoreColor(result.similarity),
                ),
              ],
            ),

            const SizedBox(height: 10),

            // ── Document preview ────────────────────────────────
            _DocumentPreview(text: result.document),

            const SizedBox(height: 10),

            // ── Metadata chips ──────────────────────────────────
            Wrap(
              spacing: 6,
              runSpacing: 4,
              children: [
                _MetaChip(icon: Icons.description, label: result.docType),
                if (result.pageInfo.isNotEmpty)
                  _MetaChip(icon: Icons.bookmark, label: result.pageInfo),
                _MetaChip(
                  icon: Icons.link,
                  label: result.source.length > 40
                      ? '${result.source.substring(0, 40)}…'
                      : result.source,
                ),
              ],
            ),

            // ── Raw distance (collapsed detail) ─────────────────
            const SizedBox(height: 6),
            Text(
              'cosine distance: ${result.distance.toStringAsFixed(4)}',
              style: theme.textTheme.bodySmall?.copyWith(
                color: theme.colorScheme.outline,
                fontSize: 11,
              ),
            ),
          ],
        ),
      ),
    );
  }
}

// ------------------------------------------------------------------ //
//  Score badge
// ------------------------------------------------------------------ //

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
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
      decoration: BoxDecoration(
        color: color.withOpacity(0.15),
        borderRadius: BorderRadius.circular(20),
        border: Border.all(color: color, width: 1.2),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(Icons.auto_awesome, size: 14, color: color),
          const SizedBox(width: 4),
          Text(
            percent,
            style: TextStyle(
              fontWeight: FontWeight.w700,
              color: color,
              fontSize: 13,
            ),
          ),
        ],
      ),
    );
  }
}

// ------------------------------------------------------------------ //
//  Document preview (expandable)
// ------------------------------------------------------------------ //

class _DocumentPreview extends StatefulWidget {
  final String text;
  const _DocumentPreview({required this.text});

  @override
  State<_DocumentPreview> createState() => _DocumentPreviewState();
}

class _DocumentPreviewState extends State<_DocumentPreview> {
  bool _expanded = false;
  static const _previewLen = 200;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final tooLong = widget.text.length > _previewLen;
    final displayText = (!tooLong || _expanded)
        ? widget.text
        : '${widget.text.substring(0, _previewLen)}…';

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          displayText,
          style: theme.textTheme.bodyMedium?.copyWith(height: 1.5),
        ),
        if (tooLong)
          GestureDetector(
            onTap: () => setState(() => _expanded = !_expanded),
            child: Padding(
              padding: const EdgeInsets.only(top: 4),
              child: Text(
                _expanded ? 'Show less' : 'Show more',
                style: TextStyle(
                  color: theme.colorScheme.primary,
                  fontWeight: FontWeight.w600,
                  fontSize: 12,
                ),
              ),
            ),
          ),
      ],
    );
  }
}

// ------------------------------------------------------------------ //
//  Metadata chip
// ------------------------------------------------------------------ //

class _MetaChip extends StatelessWidget {
  final IconData icon;
  final String label;

  const _MetaChip({required this.icon, required this.label});

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
      decoration: BoxDecoration(
        color: theme.colorScheme.surfaceContainerHighest,
        borderRadius: BorderRadius.circular(6),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(icon, size: 12, color: theme.colorScheme.onSurfaceVariant),
          const SizedBox(width: 3),
          Text(
            label,
            style: theme.textTheme.labelSmall?.copyWith(
              color: theme.colorScheme.onSurfaceVariant,
            ),
          ),
        ],
      ),
    );
  }
}
