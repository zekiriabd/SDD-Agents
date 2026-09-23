# GÉNÉRÉ par gen_source_tools.py — NE PAS ÉDITER.
# Source `payments` (file · store `support_data`) · confiance : untrusted.
# Schéma figé : workspace/src/SupportDesk/src/SupportDesk/data/schemas/payments.schema.json
# Éditer ce fichier est [DATA_TOOL_HAND_EDITED] : corriger la DÉCLARATION de la
# source, puis `gen_source_tools.py --write`. Le code et la déclaration ne peuvent
# pas diverger sans que quelqu'un s'en aperçoive — c'est tout l'intérêt.
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from SupportDesk.data.envelope import count_records
from SupportDesk.tools.spec import ToolContext, ToolSpec

SPEC = ToolSpec.from_contract("1-payments-count")

SOURCE = "payments"
PII_FIELDS = frozenset({"card_last4"})
UNTRUSTED_FIELDS = frozenset({"failure_reason"})


class Input(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    customer_id: str = Field(description="Filtre OBLIGATOIRE sur `customer_id` — égalité stricte, jamais une expression.")
    invoice_id: str | None = Field(default=None, description="Filtre optionnel sur `invoice_id` — égalité stricte, jamais une expression.")
    method: str | None = Field(default=None, description="Filtre optionnel sur `method` — égalité stricte, jamais une expression. Valeurs admises : card, paypal, bank_transfer, gift_card.")
    order_id: str | None = Field(default=None, description="Filtre optionnel sur `order_id` — égalité stricte, jamais une expression.")
    status: str | None = Field(default=None, description="Filtre optionnel sur `status` — égalité stricte, jamais une expression. Valeurs admises : authorized, captured, pending, failed, refunded.")
    attempted_at_min: str | None = Field(default=None, description="Filtre de plage sur `attempted_at` : borne basse incluse. Même type et même unité que le champ.")
    attempted_at_max: str | None = Field(default=None, description="Filtre de plage sur `attempted_at` : borne haute incluse. Même type et même unité que le champ.")
    paid_at_min: str | None = Field(default=None, description="Filtre de plage sur `paid_at` : borne basse incluse. Même type et même unité que le champ.")
    paid_at_max: str | None = Field(default=None, description="Filtre de plage sur `paid_at` : borne haute incluse. Même type et même unité que le champ.")


class Record(BaseModel):
    model_config = ConfigDict(frozen=True)

    amount: str = Field(description="Montant réglé ou tenté, EUR, chaîne à deux décimales.")
    attempted_at: str = Field(description="Date de la tentative, UTC ISO 8601.")
    card_last4: str | None = Field(description="Quatre derniers chiffres de la carte — la SEULE information de carte existante. null si method != card. Donnée personnelle : redigée dans les traces.")
    currency: str = Field(description="Devise — toujours EUR.")
    customer_id: str = Field(description="Identifiant du client payeur (CUST-NNNN). Filtre d'identité ; non énumérable.")
    failure_reason: str | None = Field(description="Motif d'échec transmis par la banque. Texte libre non maîtrisé ; null si aucun échec.")
    invoice_id: str | None = Field(description="Facture réglée. null pour une tentative échouée sur une commande jamais facturée.")
    method: str = Field(description="Moyen de paiement : card, paypal, bank_transfer, gift_card.")
    order_id: str = Field(description="Numéro de la commande réglée.")
    paid_at: str | None = Field(description="Date d'encaissement, UTC ISO 8601. null si pending ou failed.")
    payment_id: str = Field(description="Identifiant du paiement (PAY-NNNN).")
    status: str = Field(description="authorized, captured (encaissé), pending (virement attendu), failed, refunded.")


class Output(BaseModel):
    model_config = ConfigDict(frozen=True)

    count: int = Field(description="Nombre d'enregistrements correspondant aux filtres, compté côté code et non par le modèle.")
    as_of: str = Field(description="Instantané de la source, ISO 8601 UTC. La donnée a pu changer depuis.")
    stale: bool = Field(description="Vrai si l'instantané dépasse max_staleness_hours : le dire à l'utilisateur.")


async def payments_count(params: Input, *, ctx: ToolContext) -> Output:
    """Comptage filtré. Paiements, un par tentative de règlement d'une commande. Utiliser pour : dire"""
    return await count_records(
        source=SOURCE,
        filters=params.model_dump(exclude_none=True),
        output_model=Output,
        ctx=ctx,
        pii_fields=PII_FIELDS,
        untrusted_fields=UNTRUSTED_FIELDS,
    )
