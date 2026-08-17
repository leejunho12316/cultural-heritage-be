package com.aivle.conservation_backend.common.config;

import org.hibernate.cfg.MappingSettings;
import org.hibernate.type.format.jackson.Jackson3JsonFormatMapper;
import org.springframework.boot.hibernate.autoconfigure.HibernatePropertiesCustomizer;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import tools.jackson.databind.json.JsonMapper;

/**
 * Hibernate's {@code @JdbcTypeCode(SqlTypes.JSON)} columns (e.g.
 * {@code AssessmentRun.report_json}) are read/written by Hibernate's
 * own built-in Jackson format mapper, which by default builds its own bare
 * {@code ObjectMapper}/{@code JsonMapper} rather than reusing Spring's
 * autoconfigured one - so it has no java.time (Instant, etc.) support unless
 * explicitly wired here. Without this, deserializing any JSON column that
 * contains an Instant field fails at read time with
 * "Java 8 date/time type `java.time.Instant` not supported by default".
 */
@Configuration
public class HibernateJsonConfig {

    @Bean
    public HibernatePropertiesCustomizer jsonFormatMapperCustomizer(JsonMapper jsonMapper) {
        return hibernateProperties -> hibernateProperties.put(
                MappingSettings.JSON_FORMAT_MAPPER,
                new Jackson3JsonFormatMapper(jsonMapper));
    }
}
