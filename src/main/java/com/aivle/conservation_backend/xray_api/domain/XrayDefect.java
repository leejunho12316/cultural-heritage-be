package com.aivle.conservation_backend.xray_api.domain;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.FetchType;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.JoinColumn;
import jakarta.persistence.ManyToOne;
import jakarta.persistence.Table;
import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.type.SqlTypes;

import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.Map;

@Entity
@Table(name = "xray_defect")
public class XrayDefect {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "xray_job_id", nullable = false)
    private XrayJob xrayJob;

    @Enumerated(EnumType.STRING)
    @Column(name = "origin_type", nullable = false, length = 20)
    private XrayDefectOriginType originType;

    @JdbcTypeCode(SqlTypes.JSON)
    @Column(name = "geometry", nullable = false, columnDefinition = "jsonb")
    private Map<String, Object> geometry;

    @Enumerated(EnumType.STRING)
    @Column(name = "review_decision", nullable = false, length = 20)
    private XrayDefectReviewDecision reviewDecision;

    @Column(name = "created_at", nullable = false)
    private Instant createdAt;

    @Column(name = "updated_at", nullable = false)
    private Instant updatedAt;

    protected XrayDefect() {
    }

    public Long getId() {
        return id;
    }

    public XrayJob getXrayJob() {
        return xrayJob;
    }

    public XrayDefectOriginType getOriginType() {
        return originType;
    }

    public Map<String, Object> getGeometry() {
        return geometry;
    }

    public XrayDefectReviewDecision getReviewDecision() {
        return reviewDecision;
    }

    public Instant getCreatedAt() {
        return createdAt;
    }

    public Instant getUpdatedAt() {
        return updatedAt;
    }

    public static XrayDefect create(
            XrayJob job,
            XrayDefectOriginType originType,
            Map<String, Object> geometry
    ) {
        XrayDefect defect = new XrayDefect();
        Instant now = Instant.now();
        defect.xrayJob = job;
        defect.originType = originType;
        defect.geometry = new LinkedHashMap<>(geometry);
        defect.reviewDecision = XrayDefectReviewDecision.DAMAGE;
        defect.createdAt = now;
        defect.updatedAt = now;
        return defect;
    }

    public void changeReviewDecision(XrayDefectReviewDecision decision) {
        this.reviewDecision = decision;
        this.updatedAt = Instant.now();
    }
}
