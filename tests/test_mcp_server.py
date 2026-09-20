"""The MCP connector: the protocol handshake, and the limits on what it can do.

The tools are thin wrappers over functions tested elsewhere, so these tests
cover the two things that are new — that the JSON-RPC surface is right, and that
reaching a requester is still hard on purpose.
"""
import json
import unittest

from core import ledger as L, proposals as P
from tools import mcp_server as M
from tests.helpers import sandbox


def rpc(method, params=None, mid=1):
    return M.handle({"jsonrpc": "2.0", "id": mid, "method": method,
                     "params": params or {}})


def proposed(led, ticket="PESD1-11280"):
    proposal = P.Proposal(
        proposal_id=P.next_id(), ticket=ticket,
        ticket_url=f"https://pomelofashion.atlassian.net/browse/{ticket}",
        requirement_restated="Requester reports: something is wrong.",
        requester=P.Requester(), classification=P.ANSWERABLE, confidence=0.9,
        proposed_comment="Here is the answer.", flags=["requester_unknown"])
    P.save(proposal)
    led.claim(ticket, "hash")
    led.transition(ticket, L.PROPOSED, proposal_id=proposal.proposal_id)
    return proposal


class ProtocolTests(unittest.TestCase):
    def test_initialize_reports_the_protocol_and_server(self):
        r = rpc("initialize")["result"]
        self.assertEqual(r["protocolVersion"], M.PROTOCOL_VERSION)
        self.assertEqual(r["serverInfo"]["name"], M.SERVER_NAME)
        self.assertIn("tools", r["capabilities"])

    def test_a_notification_gets_no_reply(self):
        self.assertIsNone(M.handle({"jsonrpc": "2.0",
                                    "method": "notifications/initialized"}))

    def test_every_tool_is_listed_with_a_schema(self):
        tools = rpc("tools/list")["result"]["tools"]
        self.assertEqual(len(tools), len(M.TOOLS))
        for t in tools:
            self.assertTrue(t["description"], t["name"])
            self.assertEqual(t["inputSchema"]["type"], "object")

    def test_an_unknown_method_is_an_error_not_a_crash(self):
        self.assertEqual(rpc("does/not/exist")["error"]["code"], -32601)

    def test_an_unknown_tool_is_an_error(self):
        r = rpc("tools/call", {"name": "delete_everything", "arguments": {}})
        self.assertEqual(r["error"]["code"], -32602)

    def test_a_failing_tool_returns_isError_not_a_500(self):
        r = rpc("tools/call", {"name": "show_proposal",
                               "arguments": {"proposal_id": "p_nope"}})
        self.assertTrue(r["result"]["isError"])


class ToolTests(unittest.TestCase):
    def test_listing_proposals_shows_what_is_waiting(self):
        with sandbox():
            with L.Ledger() as led:
                p = proposed(led)
            out = json.loads(rpc("tools/call", {
                "name": "list_proposals", "arguments": {}})["result"]["content"][0]["text"])
            self.assertEqual(out[0]["proposal_id"], p.proposal_id)
            self.assertEqual(out[0]["ticket"], "PESD1-11280")

    def test_approving_records_the_decision(self):
        with sandbox():
            with L.Ledger() as led:
                p = proposed(led)
            rpc("tools/call", {"name": "approve",
                               "arguments": {"proposal_id": p.proposal_id}})
            with L.Ledger() as led:
                self.assertEqual(led.get("PESD1-11280")["state"], L.APPROVED)

    def test_rejecting_without_a_reason_is_refused(self):
        """A reason is what corrections.jsonl learns from; a button cannot give one."""
        with sandbox():
            with L.Ledger() as led:
                p = proposed(led)
            out = json.loads(rpc("tools/call", {
                "name": "reject",
                "arguments": {"proposal_id": p.proposal_id, "note": "  "}
            })["result"]["content"][0]["text"])
            self.assertIn("needs a reason", out["error"])
            with L.Ledger() as led:
                self.assertEqual(led.get("PESD1-11280")["state"], L.PROPOSED)

    def test_rejecting_with_a_reason_works(self):
        with sandbox():
            with L.Ledger() as led:
                p = proposed(led)
            rpc("tools/call", {"name": "reject", "arguments": {
                "proposal_id": p.proposal_id, "note": "wrong SOP, about returns"}})
            with L.Ledger() as led:
                self.assertEqual(led.get("PESD1-11280")["state"], L.REJECTED)


class ReachingPeopleTests(unittest.TestCase):
    def test_execute_is_a_dry_run_unless_confirmed(self):
        with sandbox():
            out = json.loads(rpc("tools/call", {
                "name": "execute", "arguments": {}})["result"]["content"][0]["text"])
            self.assertTrue(out["dry_run"])
            self.assertIn("confirm=true", out["note"])

    def test_execute_is_the_only_tool_that_can_write_outward(self):
        """Everything else records decisions locally."""
        self.assertEqual(
            [n for n, s in M.TOOLS.items() if "REACHES REAL PEOPLE" in s["description"]],
            ["execute"])

    def test_the_execute_schema_makes_confirmation_explicit(self):
        self.assertIn("confirm", M.TOOLS["execute"]["schema"]["properties"])


class AuthTests(unittest.TestCase):
    def test_a_wrong_bearer_token_is_rejected(self):
        class Req:
            headers = {"Authorization": "Bearer wrong"}
        h = object.__new__(M.Handler)
        h.token = "right"
        h.headers = Req.headers
        self.assertFalse(M.Handler._authorised(h))

    def test_the_right_token_is_accepted(self):
        h = object.__new__(M.Handler)
        h.token = "right"
        h.headers = {"Authorization": "Bearer right"}
        self.assertTrue(M.Handler._authorised(h))

    def test_no_configured_token_means_nobody_is_authorised(self):
        h = object.__new__(M.Handler)
        h.token = ""
        h.headers = {"Authorization": "Bearer anything"}
        self.assertFalse(M.Handler._authorised(h))

    def test_the_server_refuses_to_start_without_a_token(self):
        from core import config
        saved = config.env
        config.env = lambda n, d=None, **k: "" if n == "MCP_AUTH_TOKEN" else saved(n, d, **k)
        try:
            self.assertEqual(M.serve(port=0), 2)
        finally:
            config.env = saved


if __name__ == "__main__":
    unittest.main()
