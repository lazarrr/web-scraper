/// Model for a single similarity-search result returned by the API.
class SearchResult {
  final String document;
  final Map<String, dynamic> metadata;
  final double distance;
  final double similarity;

  const SearchResult({
    required this.document,
    required this.metadata,
    required this.distance,
    required this.similarity,
  });

  factory SearchResult.fromJson(Map<String, dynamic> json) {
    return SearchResult(
      document: json['document'] as String? ?? '',
      metadata: Map<String, dynamic>.from(json['metadata'] as Map? ?? {}),
      distance: (json['distance'] as num?)?.toDouble() ?? 1.0,
      similarity: (json['similarity'] as num?)?.toDouble() ?? 0.0,
    );
  }

  /// Human-friendly similarity percentage string, e.g. "87.3%".
  String get similarityPercent => '${(similarity * 100).toStringAsFixed(1)}%';

  /// Source filename or URL from metadata.
  String get source =>
      metadata['source']?.toString() ??
      metadata['filename']?.toString() ??
      'Unknown';

  /// Document type: pdf, docx, or web.
  String get docType => metadata['type']?.toString() ?? 'unknown';

  /// Page number (1-based) for PDFs, empty otherwise.
  String get pageInfo {
    final page = metadata['page'];
    if (page != null && page is num && page > 0) return 'p. $page';
    return '';
  }
}
