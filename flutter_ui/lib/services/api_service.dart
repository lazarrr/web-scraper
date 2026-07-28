import 'dart:convert';
import 'package:http/http.dart' as http;
import '../models/search_result.dart';

/// Full response from POST /search.
class SearchResponse {
  final String query;
  final int totalResults;
  final List<SearchResult> results;

  const SearchResponse({
    required this.query,
    required this.totalResults,
    required this.results,
  });

  factory SearchResponse.fromJson(Map<String, dynamic> json) {
    return SearchResponse(
      query: json['query'] as String? ?? '',
      totalResults: json['total_results'] as int? ?? 0,
      results:
          (json['results'] as List<dynamic>?)
              ?.map((e) => SearchResult.fromJson(e as Map<String, dynamic>))
              .toList() ??
          [],
    );
  }
}

/// Service that communicates with the vector-search FastAPI server.
class ApiService {
  final String baseUrl;

  const ApiService({required this.baseUrl});

  /// Calls POST /search with the given [query] and returns the full response.
  /// Throws on network errors or non-200 responses.
  Future<SearchResponse> search(String query, {int k = 5}) async {
    final uri = Uri.parse('$baseUrl/search');
    final body = jsonEncode({'query': query, 'k': k});

    final response = await http
        .post(uri, headers: {'Content-Type': 'application/json'}, body: body)
        .timeout(const Duration(seconds: 30));

    if (response.statusCode != 200) {
      throw ApiException(response.statusCode, response.body);
    }

    return SearchResponse.fromJson(
      jsonDecode(response.body) as Map<String, dynamic>,
    );
  }

  /// Checks if the search server is reachable.
  Future<bool> healthCheck() async {
    try {
      final uri = Uri.parse('$baseUrl/health');
      final response = await http.get(uri).timeout(const Duration(seconds: 5));
      return response.statusCode == 200;
    } catch (_) {
      return false;
    }
  }
}

class ApiException implements Exception {
  final int statusCode;
  final String body;
  const ApiException(this.statusCode, this.body);

  @override
  String toString() => 'ApiException($statusCode): $body';
}
