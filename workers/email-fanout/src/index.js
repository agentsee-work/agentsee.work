/**
 * email-fanout
 *
 * Cloudflare Email Routing can only forward a rule to a single destination —
 * one action per rule, one destination per action. That means an address like
 * hello@ can reach exactly one of us, which is no good for a shared inbox.
 *
 * An Email Worker is the way around it: routing sends the message here, and
 * here we forward it to everyone.
 *
 * Recipients come from the RECIPIENTS secret (comma-separated), never from
 * this file — the repo is public and these are personal addresses. Set it with:
 *
 *     npx wrangler secret put RECIPIENTS
 *
 * Every address must already be a *verified* destination on the Cloudflare
 * account, or forwarding to it silently does nothing.
 */

export default {
  async email(message, env, ctx) {
    const recipients = (env.RECIPIENTS || '')
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean);

    if (recipients.length === 0) {
      // Better to bounce loudly than to accept mail and drop it on the floor.
      message.setReject('No forwarding address is configured for this domain.');
      return;
    }

    // Deliver to everyone even if one address fails, rather than letting the
    // first failure decide the fate of the whole message.
    const results = await Promise.allSettled(
      recipients.map((address) => message.forward(address)),
    );

    const delivered = results.filter((r) => r.status === 'fulfilled').length;

    results.forEach((r, i) => {
      if (r.status === 'rejected') {
        console.log(`forward to ${recipients[i]} failed: ${r.reason}`);
      }
    });

    // Only reject if nobody got it. A partial delivery is still a delivery,
    // and rejecting would tell the sender their mail bounced when it didn't.
    if (delivered === 0) {
      message.setReject('Could not deliver to any recipient.');
    }
  },
};
