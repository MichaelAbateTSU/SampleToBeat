# Hosted idle health observation

Hosted tabs stop periodic health requests after one minute without pointer,
keyboard, scroll, or touch interaction, or when hidden. Returning through focus,
visibility, online connectivity, or interaction explicitly checks health and
refreshes mounted queries after successful reconnection. This does not rely on
the global query client's disabled window-focus refetch option.

Tracked nonterminal jobs retain visible-tab health observation; their existing
SSE/polling and recovery behavior is unchanged. React Query suppresses interval
health requests while unfocused, including with a nonterminal job. No hidden-tab
health keepalive is added. Hosted tabs additionally fetch actual status for
tracked nonterminal jobs every 30 seconds while hidden, with one in-flight
request per job. Terminal updates stop observation; teardown discards in-flight
results and removes timers/listeners (it does not abort an existing request).
An unresolved request blocks further requests for that job rather than piling up
traffic; it can stall observation until the network responds. Three consecutive failures pause background
observation for that job and show an error; visibility return retries immediately
and restores its failure budget. Other jobs retain independent observation.
Snapshots cannot overwrite newer SSE progress, except to reconcile terminal state.
Returning explicitly checks health and reconciles mounted job lists. Local mode
and unknown capabilities retain ten-second health polling
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
behavior is changed. Genuine status traffic may help prevent inactivity spin-down
during observed jobs, but browser suspension/closure, network failures, provider
restarts and quotas prevent any guarantee of uninterrupted work. The existing
visible-tab job recovery continues after bounded background observation pauses.
