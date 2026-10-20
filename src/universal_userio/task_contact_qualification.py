"""Internal first-contact qualification from a trusted task catalog source.

There is deliberately no HTTP or MCP registration for this module. Production
construction has no catalog origin resolver today, so qualification and every
downstream mandate operation fail closed until the supported B11/catalog
binding is supplied by trusted server code.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import math
import re
import sys
import time
from typing import ContextManager, Iterator, Protocol

from .contracts import UserPrincipal


_SAFE_TEXT = re.compile(r"[^\x00-\x1f\x7f]{1,512}\Z")
_NAMES_OR_FREE_TEXT = {"name", "display_name", "email", "phone", "free_text"}


class ContactQualificationError(ValueError):
    """A new contact is not proven for this authenticated task/account."""


@dataclass(frozen=True, slots=True)
class CatalogContactProof:
    """Server-resolved contact evidence; never constructed from tool arguments."""

    user_id: str
    task_id: str
    actor_id: str
    project_id: str
    catalog_namespace: str
    catalog_record_id: str
    catalog_contact_id: str
    catalog_revision: str
    contact_version: str
    provider: str
    account_ref: str
    provider_locator_kind: str
    provider_locator: str
    expires_at: float


@dataclass(frozen=True, slots=True)
class ProviderContactIdentity:
    """Exact account-bound identity returned by a provider's read/resolve seam."""

    provider: str
    account_ref: str
    peer_id: str
    peer_kind: str
    route_id: str
    display_name: str = ""


class CatalogAuthorityLease(Protocol):
    """Source-owned serialization lease; held through the local DB commit.

    ``bind`` may narrow an initial contact scope to the resolved provider peer
    and then to an exact operation. ``assert_current`` must fence source
    revision/revocation, lease expiry, and the full binding while the context
    manager is held. A boolean revalidation result is not an implementation of
    this protocol. If the owner cannot serialize the lease against revocation,
    it must deny acquisition.
    """

    def bind(self, binding: dict[str, object]) -> None: ...

    def assert_current(self, binding: dict[str, object]) -> None: ...


class CatalogOriginResolver(Protocol):
    """Trusted server adapter over B11/catalog provenance and task authority."""

    def resolve_contact(
        self, *, user_id: str, task_id: str, actor_id: str, contact_ref: str,
    ) -> CatalogContactProof | None: ...

    def revalidate_contact(self, qualification: dict[str, object]) -> bool: ...

    def acquire_authority_guard(
        self, *, proof: dict[str, object] | list[dict[str, object]], action: str,
        binding: dict[str, object],
    ) -> ContextManager[CatalogAuthorityLease]: ...


class TaskContactQualification:
    """Resolve and persist a new contact without invoking any send path."""

    def __init__(self, store, service=None, *,
                 catalog_origin: CatalogOriginResolver | None = None,
                 provider_resolvers: dict[str, object] | None = None) -> None:
        self.store = store
        self.service = service
        self.catalog_origin = catalog_origin
        self.provider_resolvers = dict(provider_resolvers or {})

    @staticmethod
    def _text(name: str, value: object) -> str:
        if not isinstance(value, str) or not _SAFE_TEXT.fullmatch(value.strip()):
            raise ContactQualificationError(f"invalid_{name}")
        return value.strip()

    @staticmethod
    def _canonical(value: object) -> bytes:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")

    @classmethod
    def _digest(cls, value: object) -> str:
        return hashlib.sha256(cls._canonical(value)).hexdigest()

    def _assert_issuer(self, issuer: UserPrincipal) -> None:
        if (not isinstance(issuer, UserPrincipal) or issuer.role != "owner"
                or issuer.service_account or issuer.read_only
                or issuer.messenger_accounts is not None
                or issuer.messenger_grants is not None or issuer.meeting_recorder
                or getattr(issuer, "task_mandate_id", None) is not None):
            raise PermissionError("task_contact_full_owner_authentication_required")
        row = self.store._connection.execute(
            "SELECT username,role FROM users WHERE id=?", (issuer.user_id,)
        ).fetchone()
        if (row is None or str(row["role"]) != "owner"
                or str(row["username"]).casefold() != issuer.username.casefold()):
            raise PermissionError("task_contact_full_owner_authentication_required")

    @staticmethod
    def _proof_record(proof: CatalogContactProof) -> dict[str, object]:
        return {
            "user_id": proof.user_id,
            "task_id": proof.task_id,
            "actor_id": proof.actor_id,
            "project_id": proof.project_id,
            "catalog_namespace": proof.catalog_namespace,
            "catalog_record_id": proof.catalog_record_id,
            "catalog_contact_id": proof.catalog_contact_id,
            "catalog_revision": proof.catalog_revision,
            "contact_version": proof.contact_version,
            "provider": proof.provider,
            "account_ref": proof.account_ref,
            "expires_at": float(proof.expires_at),
        }

    @staticmethod
    def _source_proof(qualification: dict[str, object]) -> dict[str, object]:
        keys = (
            "user_id", "task_id", "actor_id", "project_id", "catalog_namespace",
            "catalog_record_id", "catalog_contact_id", "catalog_revision",
            "contact_version", "provider", "account_ref", "expires_at",
        )
        return {key: qualification[key] for key in keys if key in qualification}

    @classmethod
    def authority_binding(
        cls, qualification: dict[str, object], *, action: str,
        **details: object,
    ) -> dict[str, object]:
        binding = {"action": action, **cls._source_proof(qualification)}
        binding.update(details)
        return binding

    @staticmethod
    def qualification_authority_reference(item: dict[str, object]) -> dict[str, object]:
        return {
            "user_id": str(item["user_id"]),
            "qualification_id": str(item["id"]),
            "qualification_binding_hash": str(item["binding_hash"]),
            "task_id": str(item["task_id"]),
            "actor_id": str(item["actor_id"]),
            "project_id": str(item["project_id"]),
            "catalog_namespace": str(item["catalog_namespace"]),
            "catalog_record_id": str(item["catalog_record_id"]),
            "catalog_contact_id": str(item["catalog_contact_id"]),
            "catalog_revision": str(item["catalog_revision"]),
            "contact_version": str(item["contact_version"]),
            "account_ref": str(item["account_ref"]),
            "provider": str(item["provider"]),
            "endpoint_id": str(item["endpoint_id"]),
            "peer_id": str(item["peer_id"]),
            "conversation_id": str(item["conversation_id"]),
        }

    @classmethod
    def batch_authority_binding(
        cls, qualifications: list[dict[str, object]], *, action: str,
        **details: object,
    ) -> dict[str, object]:
        references = sorted(
            (cls.qualification_authority_reference(item) for item in qualifications),
            key=lambda item: item["qualification_id"],
        )
        return {"action": action, "qualified_contacts": references, **details}

    @contextmanager
    def _authority_guard(
        self, proof: dict[str, object] | list[dict[str, object]], *, action: str,
        binding: dict[str, object],
    ) -> Iterator[CatalogAuthorityLease]:
        origin = self.catalog_origin
        acquire = getattr(origin, "acquire_authority_guard", None)
        if not callable(acquire):
            raise ContactQualificationError("catalog_authority_guard_unavailable")
        try:
            proof_payload = ([dict(item) for item in proof] if isinstance(proof, list)
                             else dict(proof))
            guard = acquire(proof=proof_payload, action=action, binding=dict(binding))
        except (ContactQualificationError, PermissionError):
            raise
        except Exception as error:
            raise ContactQualificationError("catalog_authority_guard_denied") from error
        if not callable(getattr(guard, "__enter__", None)) or not callable(
            getattr(guard, "__exit__", None)
        ):
            raise ContactQualificationError("catalog_authority_guard_invalid")
        try:
            lease = guard.__enter__()
        except (ContactQualificationError, PermissionError):
            raise
        except Exception as error:
            raise ContactQualificationError("catalog_authority_guard_denied") from error
        try:
            if (not callable(getattr(lease, "bind", None))
                    or not callable(getattr(lease, "assert_current", None))):
                raise ContactQualificationError("catalog_authority_lease_invalid")
            lease.bind(dict(binding))
            lease.assert_current(dict(binding))
        except (ContactQualificationError, PermissionError):
            guard.__exit__(*sys.exc_info())
            raise
        except Exception as error:
            guard.__exit__(*sys.exc_info())
            raise ContactQualificationError("catalog_authority_guard_denied") from error
        try:
            yield lease
        except BaseException:
            guard.__exit__(*sys.exc_info())
            raise
        else:
            guard.__exit__(None, None, None)

    @contextmanager
    def source_authority_batch_guard(
        self, qualification_ids: list[str], *, action: str,
        binding: dict[str, object], user_id: str,
    ) -> Iterator[CatalogAuthorityLease]:
        """Serialize one multi-contact grant against all source revocations."""
        if not qualification_ids or len(set(qualification_ids)) != len(qualification_ids):
            raise ContactQualificationError("catalog_authority_contacts_required")
        qualifications = []
        for qualification_id in sorted(qualification_ids):
            item = self.store.task_contact_qualification(
                qualification_id, user_id=user_id,
            )
            if item is None:
                raise ContactQualificationError("catalog_qualification_not_found")
            qualifications.append(item)
        expected = self.batch_authority_binding(qualifications, action=action)
        if any(binding.get(key) != value for key, value in expected.items()):
            raise ContactQualificationError("catalog_authority_batch_binding_conflict")
        proofs = [self._source_proof(item) for item in qualifications]
        with self._authority_guard(proofs, action=action, binding=binding) as lease:
            yield lease

    @contextmanager
    def source_authority_guard(
        self, qualification_id: str, *, action: str,
        binding: dict[str, object], user_id: str,
    ) -> Iterator[CatalogAuthorityLease]:
        """Hold the catalog owner's revocation fence over a UserIO commit."""
        if self.catalog_origin is None:
            raise ContactQualificationError("catalog_origin_unavailable")
        qualification = self.store.task_contact_qualification(
            qualification_id, user_id=user_id,
        )
        if qualification is None:
            raise ContactQualificationError("catalog_qualification_not_found")
        proof = self._source_proof(qualification)
        exact_scope = self.authority_binding(
            qualification, action=action,
            qualification_id=qualification_id,
            qualification_binding_hash=str(qualification["binding_hash"]),
            conversation_id=str(qualification["conversation_id"]),
            endpoint_id=str(qualification["endpoint_id"]),
            peer_id=str(qualification["peer_id"]),
        )
        if any(binding.get(key) != value for key, value in exact_scope.items()):
            raise ContactQualificationError("catalog_authority_binding_scope_conflict")
        with self._authority_guard(
            proof, action=action, binding=binding,
        ) as lease:
            yield lease

    def _resolve_provider(self, proof: CatalogContactProof) -> ProviderContactIdentity:
        resolver = self.provider_resolvers.get(proof.provider)
        if resolver is None and self.service is not None:
            resolver = getattr(self.service, "messenger_providers", {}).get(proof.account_ref)
        method = getattr(resolver, "resolve_catalog_contact", None)
        if callable(method):
            identity = method(
                account_ref=proof.account_ref,
                locator_kind=proof.provider_locator_kind,
                locator=proof.provider_locator,
                user_id=proof.user_id,
            )
        elif proof.provider == "vk" and self.service is not None:
            from .vk_web import VKWeb
            identity = VKWeb(self.service, proof.user_id).resolve_catalog_contact(
                proof.account_ref, proof.provider_locator_kind, proof.provider_locator,
            )
        elif proof.provider == "yandex" and self.service is not None:
            from .yandex.web import YandexWeb
            identity = YandexWeb(self.service, proof.user_id).resolve_catalog_contact(
                proof.account_ref, proof.provider_locator_kind, proof.provider_locator,
            )
        else:
            raise ContactQualificationError("provider_first_contact_resolution_unsupported")
        if not isinstance(identity, ProviderContactIdentity):
            raise ContactQualificationError("provider_contact_identity_unverified")
        return identity

    def qualify(
        self, *, issuer: UserPrincipal, task_id: str, actor_id: str,
        contact_ref: str, account_ref: str,
    ) -> dict[str, object]:
        """Resolve one opaque catalog contact and persist its exact peer identity.

        The opaque ``contact_ref`` is meaningful only to the configured trusted
        origin resolver. It is not itself a catalog ID, contact ID, peer ID,
        name, source hash, actor assertion, or authorization.
        """
        self._assert_issuer(issuer)
        task_id = self._text("task_id", task_id)
        actor_id = self._text("actor_id", actor_id)
        contact_ref = self._text("catalog_contact_reference", contact_ref)
        account_ref = self._text("account_ref", account_ref)
        origin = self.catalog_origin
        if origin is None:
            raise ContactQualificationError("catalog_origin_unavailable")

        proof = origin.resolve_contact(
            user_id=issuer.user_id, task_id=task_id,
            actor_id=actor_id, contact_ref=contact_ref,
        )
        if not isinstance(proof, CatalogContactProof):
            raise ContactQualificationError("catalog_contact_proof_missing")
        proof_fields = {
            "user_id": proof.user_id, "task_id": proof.task_id,
            "actor_id": proof.actor_id, "project_id": proof.project_id,
            "catalog_namespace": proof.catalog_namespace,
            "catalog_record_id": proof.catalog_record_id,
            "catalog_contact_id": proof.catalog_contact_id,
            "catalog_revision": proof.catalog_revision,
            "contact_version": proof.contact_version,
            "provider": proof.provider, "account_ref": proof.account_ref,
            "provider_locator_kind": proof.provider_locator_kind,
            "provider_locator": proof.provider_locator,
        }
        for name, value in proof_fields.items():
            self._text(name, value)
        if (proof.user_id != issuer.user_id or proof.task_id != task_id
                or proof.actor_id != actor_id or proof.account_ref != account_ref):
            raise ContactQualificationError("catalog_contact_scope_conflict")
        if proof.provider_locator_kind.casefold() in _NAMES_OR_FREE_TEXT:
            raise ContactQualificationError("catalog_contact_requires_provider_native_reference")
        if (isinstance(proof.expires_at, bool)
                or not isinstance(proof.expires_at, (int, float))
                or not math.isfinite(float(proof.expires_at))
                or float(proof.expires_at) <= time.time()):
            raise ContactQualificationError("catalog_contact_proof_expired")

        account = self.store._connection.execute(
            "SELECT provider,enabled,can_read,can_reply FROM provider_accounts "
            "WHERE user_id=? AND id=?",
            (issuer.user_id, account_ref),
        ).fetchone()
        if (account is not None
                and str(account["provider"]).lower() != proof.provider.lower()):
            raise ContactQualificationError("catalog_contact_scope_conflict")
        if (account is None or not bool(account["enabled"])
                or not bool(account["can_read"]) or not bool(account["can_reply"])):
            raise ContactQualificationError("qualified_contact_account_not_available")

        # A source-owned guard is mandatory before provider lookup. It blocks
        # revocation until this exact resolved identity is committed below.
        current_proof = self._proof_record(proof)
        if origin.revalidate_contact(current_proof) is not True:
            raise ContactQualificationError("catalog_contact_source_stale")
        base_binding = self.authority_binding(
            current_proof, action="qualification_persist",
            requested_account_ref=account_ref,
        )
        with self._authority_guard(
            current_proof, action="qualification_persist", binding=base_binding,
        ) as lease:
            identity = self._resolve_provider(proof)
            if (identity.provider.lower() != proof.provider.lower()
                    or identity.account_ref != account_ref
                    or identity.peer_kind != "direct"
                    or not identity.peer_id.strip() or not identity.route_id.strip()):
                raise ContactQualificationError("provider_contact_binding_conflict")

            conversation_id = "conv_" + hashlib.sha256(
                f"{issuer.user_id}\0{identity.provider.lower()}:{account_ref}:{identity.peer_id}".encode()
            ).hexdigest()[:24]
            # Proof expiry is a freshness lease, not part of immutable identity.
            identity_proof = {
                key: value for key, value in current_proof.items()
                if key != "expires_at"
            }
            binding = {
                **identity_proof,
                "peer_id": identity.peer_id,
                "peer_kind": identity.peer_kind,
                "conversation_id": conversation_id,
                "endpoint_id": identity.route_id,
            }
            binding_hash = self._digest(binding)
            qualification_id = "qual_" + binding_hash[:32]
            qualification = {
                "id": qualification_id,
                **current_proof,
                "peer_id": identity.peer_id,
                "peer_kind": identity.peer_kind,
                "endpoint_id": identity.route_id,
                "binding_hash": binding_hash,
            }
            final_authority_binding = self.authority_binding(
                qualification, action="qualification_persist",
                qualification_id=qualification_id,
                qualification_binding_hash=binding_hash,
                conversation_id=conversation_id,
                endpoint_id=identity.route_id,
                peer_id=identity.peer_id,
                peer_kind=identity.peer_kind,
                requested_account_ref=account_ref,
            )
            lease.bind(dict(final_authority_binding))
            lease.assert_current(dict(final_authority_binding))
            conversation = self.store.create_task_contact_conversation(
                user_id=issuer.user_id, qualification=qualification,
                route_id=identity.route_id, display_name=identity.display_name,
                authority_lease=lease, authority_binding=final_authority_binding,
            )
        stored = self.store.task_contact_qualification(
            qualification_id, user_id=issuer.user_id,
        )
        if (stored is None or stored["conversation_id"] != conversation["id"]
                or stored["binding_hash"] != binding_hash):
            raise ContactQualificationError("catalog_qualification_persistence_conflict")
        return {
            "qualification_id": qualification_id,
            "binding_hash": binding_hash,
            "conversation_id": str(conversation["id"]),
            "provider": identity.provider.lower(),
            "account_ref": account_ref,
            "peer_id": identity.peer_id,
            "peer_kind": identity.peer_kind,
            "endpoint_id": identity.route_id,
            "task_id": task_id,
            "actor_id": actor_id,
        }

    def verify_current(
        self, qualification_id: str, *, user_id: str, task_id: str,
        actor_id: str, account_ref: str, provider: str, peer_id: str,
        conversation_id: str, endpoint_id: str, binding_hash: str,
    ) -> dict[str, object]:
        """Revalidate current task/catalog scope and current UserIO identity."""
        if self.catalog_origin is None:
            raise ContactQualificationError("catalog_origin_unavailable")
        qualification = self.store.task_contact_qualification(
            qualification_id, user_id=user_id,
        )
        if (qualification is None
                or str(qualification["task_id"]) != task_id
                or str(qualification["actor_id"]) != actor_id
                or str(qualification["account_ref"]) != account_ref
                or str(qualification["provider"]).lower() != provider.lower()
                or str(qualification["peer_id"]) != peer_id
                or str(qualification["conversation_id"]) != conversation_id
                or str(qualification["endpoint_id"]) != endpoint_id
                or str(qualification["binding_hash"]) != binding_hash
                or float(qualification["expires_at"]) <= time.time()):
            raise ContactQualificationError("catalog_qualification_scope_conflict")
        if self.catalog_origin.revalidate_contact(dict(qualification)) is not True:
            raise ContactQualificationError("catalog_contact_source_stale")
        conversation = self.store.conversation(conversation_id, user_id=user_id)
        if (conversation is None or conversation["source"] != provider.lower()
                or conversation["account_ref"] != account_ref
                or conversation["peer_id"] != peer_id
                or conversation["route_id"] != endpoint_id):
            raise ContactQualificationError("qualified_contact_conversation_conflict")
        return dict(qualification)
