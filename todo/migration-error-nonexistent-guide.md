# Migration error message points to a nonexistent guide

## Context

Found during a code audit (2026-07) of error remediation text.

## Problem

A migration-related error message directs the user to a guide/document that does not exist
(dead path or never-written doc). The error fires exactly when the user most needs help — mid
format change — and hands them a dead end.

## Suggested fix

1. Locate the error message and the referenced guide path/URL.
2. Either write the referenced guide or rewrite the message to contain the actual remediation
   inline (the concrete steps or command to run) — inline remediation is the better default:
   it cannot rot separately from the code.
3. Grep all error messages for doc references and verify each target exists; add a test or
   lint that asserts referenced paths resolve, so future messages cannot point into the void.

## Affected area

Migration/version error messages.

## Effort

Small.
