# Hosted idle health observation

Hosted tabs stop periodic health requests after one minute without pointer,
keyboard, scroll, or touch interaction, or when hidden. Returning through focus,
visibility, online connectivity, or interaction explicitly checks health and
refreshes mounted queries after successful reconnection. This does not rely on
the global query client's disabled window-focus refetch option.

Tracked nonterminal jobs retain health observation; their existing SSE/polling
and recovery behavior is unchanged. Local mode retains ten-second health
polling. Outage messages remain visible while idle and explain when reconnection
is paused rather than claiming continuous retries.

This removes an avoidable health keepalive from otherwise inactive hosted tabs.
It does not guarantee Render will sleep, Neon quota savings, or uptime: active
jobs, other tabs, requests, integrations, and provider lifecycle policies still
affect activity. No provider keepalive or durable lease behavior is changed.
