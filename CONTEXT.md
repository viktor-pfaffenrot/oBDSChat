# oBDSChat

oBDSChat answers questions about the German oncological basic dataset and the
guidance used to document and report cancer cases.

## Language

**oBDS**:
The bundeseinheitlicher onkologischer Basisdatensatz defines the information
reported about cancer diagnoses, treatment, and follow-up, including additional
organ-specific modules.

**oBDS XML schema**:
The versioned formal definition of the XML structure and permitted values of an
oBDS report.
_Avoid_: Manual version, documentation rule

**Umsetzungsleitfaden**:
The Plattform § 65c guidance for implementing the oBDS interface.

**Manual Plus**:
The digital documentation guide published by the Plattform § 65c for cancer
registry reporting, including documentation rules and registry-specific guidance.
_Avoid_: Umsetzungsleitfaden

**Documentation rule**:
A statement about how a clinical circumstance should be documented in cancer
registry reporting.
_Avoid_: XML constraint, registry validation status

**Registry validation status**:
A cancer registry's assessment of a Manual Plus page: positively validated,
positively validated with registry-specific qualifications, negatively validated,
or not yet reviewed. The source legend assigns this assessment to a page, not
independently to each documentation rule on that page.
_Avoid_: Rule approval, XML validation

**Registry-specific qualification**:
An explanation or exception supplied for a particular cancer registry alongside
the general guidance on a Manual Plus page. It forms part of the evidence needed
to interpret that page for the registry.

**Manual Plus page revision**:
A particular edited state of a Manual Plus page. It is distinct from a published
Manual Plus edition, an oBDS XML schema version, and the date a rule applies.
_Avoid_: oBDS version, effective date

**Decision PDF**:
A PDF linked under Manual Plus: Beschlüsse that records a documentation decision.
Its adopted decision is distinct from any proposals or examples included in the
same document.

**Change highlight**:
A turquoise marking in Manual Plus that identifies a change from the previous
version. The marking alone does not establish when a documentation rule became
applicable.
_Avoid_: Effective date
