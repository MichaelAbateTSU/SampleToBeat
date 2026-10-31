# Hosted idle health observation

Hosted tabs stop periodic health requests after one minute without pointer,
keyboard, scroll, or touch interaction, or when hidden. Returning through focus,
visibility, online connectivity, or interaction explicitly checks health and
refreshes mounted queries after successful reconnection. This does not rely on
the global query client's disabled window-focus refetch option.

Tracked nonterminal jobs retain visible-tab health observation; their existing
SSE/polling and recovery behavior is unchanged. React Query suppresses interval
health requests while unfocused, including with a nonterminal job. No hidden-tab
keepalive is guaranteed; returning explicitly checks health and reconciles mounted
job lists. Local mode and unknown capabilities retain ten-second health polling
while focused, including initial-backend-outage recovery without interaction.
Idle behavior activates only after hosted mode is confirmed. Outage messages
remain visible while idle and explain when reconnection is paused rather than
claiming continuous retries.

This removes an avoidable health keepalive from otherwise inactive hosted tabs.
It does not guarantee Render will sleep, Neon quota savings, or uptime: active
jobs, other tabs, requests, integrations, and provider lifecycle policies still
affect activity. A stale nonterminal job can keep visible-tab health polling
active until job tracking reconciles its terminal status; this policy does not
guarantee sleep for unresolved jobs. No provider keepalive or durable lease
behavior is changed.
