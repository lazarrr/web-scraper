import 'package:flutter/material.dart';
import '../models/chat_message.dart';
import '../services/api_service.dart';
import '../widgets/chat_bubble.dart';
import '../widgets/settings_dialog.dart';

/// Chat-style screen for querying the vector DB and viewing results.
class ChatScreen extends StatefulWidget {
  const ChatScreen({super.key});

  @override
  State<ChatScreen> createState() => _ChatScreenState();
}

class _ChatScreenState extends State<ChatScreen> {
  final _queryCtrl = TextEditingController();
  final _scrollCtrl = ScrollController();
  final _focusNode = FocusNode();

  ApiService _api = const ApiService(baseUrl: 'http://0.0.0.0:8001');
  int _k = 5;

  final List<ChatExchange> _exchanges = [];
  bool _serverOnline = false;
  bool _sending = false;

  @override
  void initState() {
    super.initState();
    _checkHealth();
  }

  @override
  void dispose() {
    _queryCtrl.dispose();
    _scrollCtrl.dispose();
    _focusNode.dispose();
    super.dispose();
  }

  // ------------------------------------------------------------------ //
  //  Health check
  // ------------------------------------------------------------------ //

  Future<void> _checkHealth() async {
    final ok = await _api.healthCheck();
    if (mounted) setState(() => _serverOnline = ok);
  }

  // ------------------------------------------------------------------ //
  //  Send query
  // ------------------------------------------------------------------ //

  Future<void> _sendQuery() async {
    final query = _queryCtrl.text.trim();
    if (query.isEmpty || _sending) return;

    _queryCtrl.clear();
    _focusNode.unfocus();

    // Add loading placeholder
    setState(() {
      _exchanges.add(ChatExchange.loading(query));
      _sending = true;
    });
    _scrollToBottom();

    try {
      final response = await _api.search(query, k: _k);

      if (mounted) {
        setState(() {
          // Replace loading placeholder with real results
          _exchanges.removeLast();
          _exchanges.add(
            ChatExchange(
              query: query,
              results: response.results,
              timestamp: DateTime.now(),
            ),
          );
          _sending = false;
        });
      }
    } on ApiException catch (e) {
      if (mounted) {
        setState(() {
          _exchanges.removeLast();
          _exchanges.add(
            ChatExchange.error(query, 'Server error (${e.statusCode})'),
          );
          _sending = false;
        });
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _exchanges.removeLast();
          _exchanges.add(
            ChatExchange.error(query, 'Is the search server running?\n$e'),
          );
          _sending = false;
        });
      }
    }

    _scrollToBottom();
  }

  void _scrollToBottom() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_scrollCtrl.hasClients) {
        _scrollCtrl.animateTo(
          _scrollCtrl.position.maxScrollExtent,
          duration: const Duration(milliseconds: 300),
          curve: Curves.easeOut,
        );
      }
    });
  }

  // ------------------------------------------------------------------ //
  //  Settings
  // ------------------------------------------------------------------ //

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

  // ------------------------------------------------------------------ //
  //  Build
  // ------------------------------------------------------------------ //

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);

    return Scaffold(
      appBar: AppBar(
        title: Row(
          children: [
            Icon(Icons.memory, size: 22, color: theme.colorScheme.primary),
            const SizedBox(width: 8),
            const Text('Vector DB Chat'),
          ],
        ),
        actions: [
          // Server status
          Tooltip(
            message: _serverOnline ? 'Server online' : 'Server offline',
            child: Padding(
              padding: const EdgeInsets.only(right: 4),
              child: Icon(
                Icons.circle,
                size: 10,
                color: _serverOnline ? Colors.green : Colors.redAccent,
              ),
            ),
          ),
          IconButton(
            icon: const Icon(Icons.settings),
            tooltip: 'Settings',
            onPressed: _openSettings,
          ),
          IconButton(
            icon: const Icon(Icons.delete_outline),
            tooltip: 'Clear chat',
            onPressed: _exchanges.isEmpty
                ? null
                : () => setState(() => _exchanges.clear()),
          ),
        ],
      ),
      body: Column(
        children: [
          // ── Chat messages ──────────────────────────────
          Expanded(
            child: _exchanges.isEmpty
                ? _buildEmptyState(theme)
                : ListView.builder(
                    controller: _scrollCtrl,
                    padding: const EdgeInsets.only(top: 12, bottom: 8),
                    itemCount: _exchanges.length,
                    itemBuilder: (_, i) {
                      final exchange = _exchanges[i];
                      return Column(
                        crossAxisAlignment: CrossAxisAlignment.stretch,
                        children: [
                          // User query bubble
                          UserBubble(
                            text: exchange.query,
                            timeStr:
                                '${exchange.timestamp.hour.toString().padLeft(2, '0')}:${exchange.timestamp.minute.toString().padLeft(2, '0')}',
                          ),
                          // Server response
                          ResponseGroup(exchange: exchange),
                          if (i < _exchanges.length - 1)
                            const Divider(height: 1, indent: 60, endIndent: 24),
                        ],
                      );
                    },
                  ),
          ),

          // ── Input bar ──────────────────────────────────
          _buildInputBar(theme),
        ],
      ),
    );
  }

  Widget _buildEmptyState(ThemeData theme) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(32),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Container(
              padding: const EdgeInsets.all(24),
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                color: theme.colorScheme.primaryContainer.withOpacity(0.5),
              ),
              child: Icon(
                Icons.chat_bubble_outline,
                size: 56,
                color: theme.colorScheme.primary,
              ),
            ),
            const SizedBox(height: 20),
            Text(
              'Vector DB Chat',
              style: theme.textTheme.titleLarge?.copyWith(
                fontWeight: FontWeight.bold,
              ),
            ),
            const SizedBox(height: 8),
            Text(
              'Ask a question to search your vector database.\nResults are ranked by similarity score.',
              textAlign: TextAlign.center,
              style: theme.textTheme.bodyMedium?.copyWith(
                color: theme.colorScheme.outline,
                height: 1.5,
              ),
            ),
            const SizedBox(height: 24),
            _ServerStatusBadge(online: _serverOnline, theme: theme),
          ],
        ),
      ),
    );
  }

  Widget _buildInputBar(ThemeData theme) {
    return Container(
      padding: const EdgeInsets.fromLTRB(12, 8, 12, 12),
      decoration: BoxDecoration(
        color: theme.colorScheme.surface,
        boxShadow: [
          BoxShadow(
            color: Colors.black.withOpacity(0.06),
            blurRadius: 8,
            offset: const Offset(0, -2),
          ),
        ],
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.end,
        children: [
          Expanded(
            child: TextField(
              controller: _queryCtrl,
              focusNode: _focusNode,
              minLines: 1,
              maxLines: 4,
              textInputAction: TextInputAction.send,
              onSubmitted: (_) => _sendQuery(),
              decoration: InputDecoration(
                hintText: 'Search the vector database…',
                prefixIcon: const Icon(Icons.search, size: 20),
                border: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(24),
                ),
                contentPadding: const EdgeInsets.symmetric(
                  horizontal: 18,
                  vertical: 12,
                ),
                filled: true,
                fillColor: theme.colorScheme.surfaceContainerHighest
                    .withOpacity(0.4),
              ),
            ),
          ),
          const SizedBox(width: 8),
          Container(
            decoration: BoxDecoration(
              color: _sending
                  ? theme.colorScheme.primary.withOpacity(0.4)
                  : theme.colorScheme.primary,
              shape: BoxShape.circle,
            ),
            child: IconButton(
              onPressed: _sending ? null : _sendQuery,
              icon: _sending
                  ? const SizedBox(
                      width: 18,
                      height: 18,
                      child: CircularProgressIndicator(
                        strokeWidth: 2,
                        color: Colors.white,
                      ),
                    )
                  : const Icon(
                      Icons.send_rounded,
                      color: Colors.white,
                      size: 20,
                    ),
            ),
          ),
        ],
      ),
    );
  }
}

// ==================================================================== //
//  Server status badge for empty state
// ==================================================================== //

class _ServerStatusBadge extends StatelessWidget {
  final bool online;
  final ThemeData theme;

  const _ServerStatusBadge({required this.online, required this.theme});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
      decoration: BoxDecoration(
        color: online
            ? Colors.green.withOpacity(0.1)
            : Colors.redAccent.withOpacity(0.1),
        borderRadius: BorderRadius.circular(20),
        border: Border.all(
          color: online
              ? Colors.green.withOpacity(0.3)
              : Colors.redAccent.withOpacity(0.3),
        ),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(
            Icons.circle,
            size: 8,
            color: online ? Colors.green : Colors.redAccent,
          ),
          const SizedBox(width: 8),
          Text(
            online ? 'Server connected' : 'Server offline',
            style: TextStyle(
              fontSize: 13,
              color: online ? Colors.green.shade700 : Colors.redAccent.shade700,
              fontWeight: FontWeight.w500,
            ),
          ),
        ],
      ),
    );
  }
}
