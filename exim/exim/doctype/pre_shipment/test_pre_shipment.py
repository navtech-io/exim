# Copyright (c) 2022, FinByz Tech Pvt Ltd and Contributors
# See license.txt

import unittest
from unittest.mock import MagicMock, patch

import frappe

from exim.exim.doctype.pre_shipment.pre_shipment import PreShipment


class TestPreShipment(unittest.TestCase):
	def _make_loan(self, **overrides):
		loan = MagicMock(spec=PreShipment)
		loan.company = "Kaveri Export"
		loan.posting_date = "2026-09-29"
		loan.loan_account = "Test Loan Account - KE"
		loan.loan_credit_account = "Test Credit Account - KE"
		loan.credit_currency = "USD"
		loan.loan_amount = 8383.23
		loan.loan_amount_inr = 699999.70
		loan.source_exchange_rate = 83.5
		loan.bank_loan_reference = "REF-1"
		loan.running = 0
		loan.against = "Sales Order"
		loan.document = "SAL-ORD-TEST"
		loan.loan_tenure = 90
		loan.loan_due_date = "2026-12-28"
		loan.journal_entry = None
		for k, v in overrides.items():
			setattr(loan, k, v)
		# create_jv() calls self.get_account_currency_and_rate(...) - bind the
		# real implementation onto the mock instead of letting it auto-mock.
		loan.get_account_currency_and_rate = (
			lambda account, company_currency: PreShipment.get_account_currency_and_rate(
				loan, account, company_currency
			)
		)
		return loan

	def _make_fake_jv(self):
		fake_jv = MagicMock()
		fake_jv.accounts = []
		fake_jv.multi_currency = 0
		fake_jv.append.side_effect = lambda _table, row: fake_jv.accounts.append(row)
		return fake_jv

	def _get_cached_value_for(self, company_currency, account_currencies):
		"""account_currencies: {account_name: currency}."""

		def _fake(doctype, name, field):
			if doctype == "Company":
				return company_currency
			return account_currencies.get(name)

		return _fake

	def test_both_accounts_in_inr_unchanged(self):
		"""Regression: the path every currently-submitted real Pre Shipment
		in Kaveri Export uses (both legs genuinely INR) must be unaffected."""
		loan = self._make_loan()
		fake_jv = self._make_fake_jv()
		get_cached_value = self._get_cached_value_for(
			"INR", {loan.loan_account: "INR", loan.loan_credit_account: "INR"}
		)

		with patch.object(frappe, "new_doc", return_value=fake_jv), patch.object(
			frappe, "get_cached_value", side_effect=get_cached_value
		):
			PreShipment.create_jv(loan)

		loan_leg, credit_leg = fake_jv.accounts
		self.assertEqual(loan_leg["account_currency"], "INR")
		self.assertEqual(loan_leg["credit_in_account_currency"], loan.loan_amount_inr)
		self.assertEqual(loan_leg["credit"], loan.loan_amount_inr)
		self.assertEqual(credit_leg["account_currency"], "INR")
		self.assertEqual(credit_leg["debit_in_account_currency"], loan.loan_amount_inr)
		self.assertEqual(credit_leg["debit"], loan.loan_amount_inr)

	def test_loan_account_inr_credit_account_foreign_currency(self):
		"""The real bug scenario: an INR PCFC liability account (loan_account)
		disbursing into a genuinely foreign-currency EEFC account
		(loan_credit_account) - both legs must independently reflect their
		own real currency, not assume the loan's own credit_currency."""
		loan = self._make_loan()
		fake_jv = self._make_fake_jv()
		get_cached_value = self._get_cached_value_for(
			"INR", {loan.loan_account: "INR", loan.loan_credit_account: "USD"}
		)

		with patch.object(frappe, "new_doc", return_value=fake_jv), patch.object(
			frappe, "get_cached_value", side_effect=get_cached_value
		):
			PreShipment.create_jv(loan)

		loan_leg, credit_leg = fake_jv.accounts
		self.assertEqual(loan_leg["account_currency"], "INR")
		self.assertEqual(loan_leg["credit_in_account_currency"], loan.loan_amount_inr)
		self.assertEqual(loan_leg["credit"], loan.loan_amount_inr)

		self.assertEqual(credit_leg["account_currency"], "USD")
		self.assertAlmostEqual(credit_leg["debit_in_account_currency"], loan.loan_amount, places=2)
		self.assertEqual(credit_leg["debit"], loan.loan_amount_inr)

		# balanced: both legs' company-currency amount must match
		self.assertEqual(loan_leg["credit"], credit_leg["debit"])
		self.assertTrue(fake_jv.multi_currency)

	def test_both_accounts_in_loan_currency(self):
		"""Both legs genuinely USD, matching credit_currency."""
		loan = self._make_loan()
		fake_jv = self._make_fake_jv()
		get_cached_value = self._get_cached_value_for(
			"INR", {loan.loan_account: "USD", loan.loan_credit_account: "USD"}
		)

		with patch.object(frappe, "new_doc", return_value=fake_jv), patch.object(
			frappe, "get_cached_value", side_effect=get_cached_value
		):
			PreShipment.create_jv(loan)

		loan_leg, credit_leg = fake_jv.accounts
		self.assertEqual(loan_leg["account_currency"], "USD")
		self.assertAlmostEqual(loan_leg["credit_in_account_currency"], loan.loan_amount, places=2)
		self.assertEqual(credit_leg["account_currency"], "USD")
		self.assertAlmostEqual(credit_leg["debit_in_account_currency"], loan.loan_amount, places=2)
		self.assertEqual(loan_leg["credit"], credit_leg["debit"])
		self.assertTrue(fake_jv.multi_currency)

	def test_credit_account_in_third_currency_converts_via_fresh_rate(self):
		"""Rarer case: loan_credit_account's currency differs from both
		company currency and credit_currency."""
		loan = self._make_loan(credit_currency="USD")
		fake_jv = self._make_fake_jv()
		get_cached_value = self._get_cached_value_for(
			"INR", {loan.loan_account: "INR", loan.loan_credit_account: "EUR"}
		)

		with patch.object(frappe, "new_doc", return_value=fake_jv), patch.object(
			frappe, "get_cached_value", side_effect=get_cached_value
		), patch(
			"exim.exim.doctype.pre_shipment.pre_shipment.get_exchange_rate", return_value=90.0
		):
			PreShipment.create_jv(loan)

		_loan_leg, credit_leg = fake_jv.accounts
		self.assertEqual(credit_leg["account_currency"], "EUR")
		self.assertEqual(credit_leg["exchange_rate"], 90.0)
		self.assertAlmostEqual(
			credit_leg["debit_in_account_currency"], loan.loan_amount_inr / 90.0, places=2
		)
		self.assertEqual(credit_leg["debit"], loan.loan_amount_inr)
		self.assertTrue(fake_jv.multi_currency)
