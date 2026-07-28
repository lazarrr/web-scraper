import 'search_result.dart';

/// A single exchange in the chat: the user's query and the server's results.
class ChatExchange {
  final String query;
  final List<SearchResult> results;
  final DateTime timestamp;
  final bool isLoading;
  final String? error;

  const ChatExchange({
    required this.query,
    this.results = const [],
    required this.timestamp,
    this.isLoading = false,
    this.error,
  });

  /// Creates a placeholder exchange while waiting for the server.
  factory ChatExchange.loading(String query) =>
      ChatExchange(query: query, timestamp: DateTime.now(), isLoading: true);

  /// Creates a failed exchange.
  factory ChatExchange.error(String query, String error) =>
      ChatExchange(query: query, timestamp: DateTime.now(), error: error);

  bool get isError => error != null;
  bool get isEmpty => !isLoading && !isError && results.isEmpty;
}
