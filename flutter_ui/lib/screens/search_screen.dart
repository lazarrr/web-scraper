import 'package:flutter/material.dart';
import '../models/search_result.dart';
import '../services/api_service.dart';
import '../widgets/result_card.dart';
import '../widgets/settings_dialog.dart';

/// Main screen: search bar at top, scrollable result cards below.
class SearchScreen extends StatefulWidget {
  const SearchScreen({super.key});

  @override
  State<SearchScreen> createState() => _SearchScreenState();
}

class _SearchScreenState extends State<SearchScreen> {
  final _queryCtrl = TextEditingController();
  final _scrollCtrl = ScrollController();

  ApiService _api = const ApiService(baseUrl: 'http://0.0.0.0:8001');
  int _k = 5;

  List<SearchResult> _results = [];
  bool _loading = false;
  String? _error;
  bool _serverOnline = false;

  @override
  void initState() {
    super.initState();
    _checkHealth();
  }

  @override
  void dispose() {
    _queryCtrl.dispose();
    _scrollCtrl.dispose();
    super.dispose();
  }

  Future<void> _checkHealth() async {
    final ok = await _api.healthCheck();
    if (mounted) setState(() => _serverOnline = ok);
  }

  Future<void> _search() async {
    final query = _queryCtrl.text.trim();
    if (query.isEmpty) return;

    setState(() {
      _loading = true;
      _error = null;
      _results = [];
    });

    try {
      final response = await _api.search(query, k: _k);
      if (mounted) {
        setState(() {
          _results = response.results;
          _loading = false;
        });
      }
    } on ApiException catch (e) {
      if (mounted) {
        setState(() {
          _loading = false;
          _error = 'Server error (${e.statusCode})';
        });
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _loading = false;
          _error = 'Connection failed — is the search server running?\n$e';
        });
      }
    }
  }

  void _openSettings() {
    showDialog(
      context: context,
      builder: (_) => SettingsDialog(
        currentBaseUrl: _api.baseUrl,
        currentK: _k,
        onSave: (url, k) {
          setState(() {
            _api = ApiService(baseUrl: url);
            _k = k;
          });
          _checkHealth();
        },
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);

    return Scaffold(
      appBar: AppBar(
        title: const Text('Vector DB Search'),
        actions: [
          // Server status indicator
          Padding(
            padding: const EdgeInsets.only(right: 4),
            child: Icon(
              Icons.circle,
              size: 10,
              color: _serverOnline ? Colors.green : Colors.redAccent,
            ),
          ),
          IconButton(
            icon: const Icon(Icons.settings),
            tooltip: 'Settings',
            onPressed: _openSettings,
          ),
        ],
      ),
      body: Column(
        children: [
          // ── Search bar ────────────────────────────────────────
          Container(
            padding: const EdgeInsets.fromLTRB(16, 12, 16, 8),
            color: theme.colorScheme.surface,
            child: Row(
              children: [
                Expanded(
                  child: TextField(
                    controller: _queryCtrl,
                    decoration: InputDecoration(
                      hintText: 'Search the vector database…',
                      prefixIcon: const Icon(Icons.search),
                      border: OutlineInputBorder(
                        borderRadius: BorderRadius.circular(28),
                      ),
                      contentPadding: const EdgeInsets.symmetric(
                        horizontal: 18,
                        vertical: 12,
                      ),
                      filled: true,
                      fillColor: theme.colorScheme.surfaceContainerHighest
                          .withOpacity(0.5),
                    ),
                    textInputAction: TextInputAction.search,
                    onSubmitted: (_) => _search(),
                  ),
                ),
                const SizedBox(width: 10),
                FilledButton(
                  onPressed: _loading ? null : _search,
                  style: FilledButton.styleFrom(
                    padding: const EdgeInsets.symmetric(
                      horizontal: 20,
                      vertical: 14,
                    ),
                    shape: RoundedRectangleBorder(
                      borderRadius: BorderRadius.circular(28),
                    ),
                  ),
                  child: _loading
                      ? const SizedBox(
                          width: 18,
                          height: 18,
                          child: CircularProgressIndicator(
                            strokeWidth: 2,
                            color: Colors.white,
                          ),
                        )
                      : const Text('Search'),
                ),
              ],
            ),
          ),

          // ── Error banner ──────────────────────────────────────
          if (_error != null)
            Container(
              width: double.infinity,
              margin: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
              padding: const EdgeInsets.all(12),
              decoration: BoxDecoration(
                color: theme.colorScheme.errorContainer,
                borderRadius: BorderRadius.circular(10),
              ),
              child: Row(
                children: [
                  Icon(Icons.error_outline, color: theme.colorScheme.error),
                  const SizedBox(width: 8),
                  Expanded(
                    child: Text(
                      _error!,
                      style: TextStyle(
                        color: theme.colorScheme.onErrorContainer,
                        fontSize: 13,
                      ),
                    ),
                  ),
                ],
              ),
            ),

          // ── Results count ─────────────────────────────────────
          if (!_loading && _queryCtrl.text.trim().isNotEmpty && _error == null)
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 6),
              child: Row(
                children: [
                  Text(
                    _results.isEmpty
                        ? 'No results found'
                        : '${_results.length} result${_results.length == 1 ? '' : 's'}',
                    style: theme.textTheme.bodySmall?.copyWith(
                      color: theme.colorScheme.outline,
                    ),
                  ),
                  const Spacer(),
                  Text('k=$_k', style: theme.textTheme.bodySmall),
                ],
              ),
            ),

          // ── Results list ──────────────────────────────────────
          Expanded(
            child: _loading
                ? const Center(child: CircularProgressIndicator())
                : _results.isEmpty
                ? Center(
                    child: Column(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Icon(
                          Icons.search_off,
                          size: 56,
                          color: theme.colorScheme.outline,
                        ),
                        const SizedBox(height: 12),
                        Text(
                          'Enter a query and press Search',
                          style: TextStyle(color: theme.colorScheme.outline),
                        ),
                      ],
                    ),
                  )
                : ListView.builder(
                    controller: _scrollCtrl,
                    padding: const EdgeInsets.only(top: 4, bottom: 24),
                    itemCount: _results.length,
                    itemBuilder: (_, i) =>
                        ResultCard(result: _results[i], index: i),
                  ),
          ),
        ],
      ),
    );
  }
}
