from conftest import SEEDED, run_seed


def snapshot(fhir) -> dict:
    """What the seed made, with every resource's version so an overwrite would show."""
    return {
        resource_type: sorted((r["id"], r["meta"]["versionId"]) for r in fhir.seeded(resource_type))
        for resource_type in SEEDED
    }


def test_running_the_seed_again_changes_no_resource(fhir, seeded_today):
    before = snapshot(fhir)
    assert len(before["Patient"]) == 60

    run_seed()
    run_seed()

    assert snapshot(fhir) == before
