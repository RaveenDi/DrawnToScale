from .storage import Conflict, EDITABLE


def export_geojson(run):
    if run["status"] not in EDITABLE:
        raise Conflict("Export is available after extraction and review preparation")
    candidates = {c["id"]: c for c in run["candidates"]}
    features, unresolved = [], []
    for landmark in run["graph"]["landmarks"]:
        decision = run["confirmations"].get(landmark["id"])
        if decision is None or decision["candidate_id"] is None:
            unresolved.append({"sketch_id": landmark["id"], "label": landmark["label"],
                               "status": "unmatched" if decision else "needs_review"})
            continue
        candidate = candidates[decision["candidate_id"]]
        features.append({"type": "Feature", "id": landmark["id"],
            "geometry": {"type": "Point", "coordinates": [candidate["longitude"], candidate["latitude"]]},
            "properties": {"label": landmark["label"], "status": "confirmed", "sketch_id": landmark["id"],
                "candidate_id": candidate["id"], "candidate_label": candidate["label"],
                "source_url": candidate["source_url"], "position_kind": candidate["position_kind"],
                "evidence": landmark["evidence"], "confirmed_by": "user", "note": decision["note"],
                "attribution": run["candidate_manifest"].get("attribution"),
                "license_url": run["candidate_manifest"].get("license_url"),
                "retrieved_at": run["candidate_manifest"].get("retrieved_at")}})
    return {"type": "FeatureCollection", "features": features, "metadata": {
        "run_id": run["id"], "revision": run["revision"], "area": run["area"],
        "image_sha256": run["image"]["sha256"], "candidate_manifest": run["candidate_manifest"],
        "unresolved_landmarks": unresolved, "unresolved_observations": run["graph"]["unresolved"],
        "alignment": run["alignment"], "telemetry": run["telemetry"],
        "note": "Only user-confirmed candidate points are exported. No inferred roads or precise boundaries. Fit error is not independent accuracy."}}


def review_report(run):
    exported = export_geojson(run)
    # Plain text avoids interpreting arbitrary sketch labels as HTML/Markdown.
    lines = ["DRAWN TO SCALE — REVIEW SUMMARY", f"Run: {run['id']} / revision {run['revision']}",
             f"Confirmed points: {len(exported['features'])}",
             f"Unresolved landmarks: {len(exported['metadata']['unresolved_landmarks'])}",
             "Independent location accuracy: NOT MEASURED", "",
             run["candidate_manifest"].get("attribution", ""),
             run["candidate_manifest"].get("license_url", ""), ""]
    for p in run["graph"]["landmarks"]:
        decision = run["confirmations"].get(p["id"])
        state = "needs review" if decision is None else "confirmed" if decision["candidate_id"] else "unmatched"
        lines.append(f"{p['id']}: {p['label']} — {state}")
    lines.extend(["", "SOURCES"])
    for feature in exported["features"]:
        source = feature["properties"]
        lines.append(f"{source['sketch_id']}: {source['source_url']} ({source['position_kind']})")
    alignment = run["alignment"] or {"status": "not_fitted"}
    lines.extend(["", "ALIGNMENT", f"Status: {alignment['status']}"])
    for anchor in alignment.get("anchors", []):
        lines.append(f"{anchor['sketch_id']}: fitting residual {anchor['fit_residual_m']} m; inlier={anchor['inlier']}")
    if alignment.get("reason"):
        lines.append(alignment["reason"])
    lines.extend(["", *run["graph"]["unresolved"], "", exported["metadata"]["note"]])
    return "\n".join(lines)
