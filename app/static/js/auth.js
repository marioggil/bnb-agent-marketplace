/* One-click wallet connect (personal_sign + session cookie).
 *
 * Flow: eth_requestAccounts -> GET /auth/nonce -> personal_sign the message
 * -> POST /auth/verify -> reload. The session cookie is HttpOnly; fetch with
 * same-origin credentials stores it automatically.
 *
 * When no EIP-1193 provider is present the button keeps working as a link to
 * /auth (which now renders the same button plus setup instructions).
 */
(function () {
  "use strict";

  function setState(btn, busy) {
    if (!btn) return;
    btn.disabled = busy;
    btn.textContent = busy ? "Connecting\u2026" : "Connect wallet";
  }

  function showError(message) {
    var box = document.getElementById("auth-error");
    if (!box) return;
    box.textContent = message;
    box.hidden = false;
  }

  async function connect(btn) {
    setState(btn, true);
    showError("");
    try {
      if (typeof window.ethereum === "undefined") {
        window.location.href = "/auth";
        return;
      }
      var accounts = await window.ethereum.request({ method: "eth_requestAccounts" });
      var address = accounts && accounts[0];
      if (!address) throw new Error("No account selected in your wallet.");

      var nonceRes = await fetch("/auth/nonce?address=" + encodeURIComponent(address));
      if (!nonceRes.ok) throw new Error("Could not request the sign-in nonce (HTTP " + nonceRes.status + ").");
      var nonceBody = await nonceRes.json();

      var signature = await window.ethereum.request({
        method: "personal_sign",
        params: [nonceBody.message, address],
      });

      var verify = await fetch("/auth/verify", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({
          address: address,
          signature: signature,
          nonce: nonceBody.nonce,
        }),
      });
      if (!verify.ok) throw new Error("Sign-in failed (HTTP " + verify.status + ").");

      window.location.reload();
    } catch (err) {
      setState(btn, false);
      var message = (err && err.message) ? err.message : String(err);
      // User rejected the signature — keep the button usable, no scary text.
      if (/rejected|denied|denied by user|user rejected/i.test(message)) {
        message = "Signature rejected. Click again to retry.";
      }
      showError(message);
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    var btn = document.getElementById("connect-wallet");
    if (!btn) return;
    btn.addEventListener("click", function (event) {
      // When a provider exists we handle the flow; otherwise fall through
      // to the /auth link navigation (progressive enhancement).
      if (typeof window.ethereum !== "undefined") {
        event.preventDefault();
        connect(btn);
      }
    });
  });
})();