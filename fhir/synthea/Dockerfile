FROM eclipse-temurin:21-jre

ARG SYNTHEA_VERSION=v4.0.0
ARG SYNTHEA_SHA256=ed43c20ad40ba5c3bc724503a5af032715fe3c491620b766148e7c2361e6ecc1

ADD --checksum=sha256:${SYNTHEA_SHA256} \
    https://github.com/synthetichealth/synthea/releases/download/${SYNTHEA_VERSION}/synthea-with-dependencies.jar \
    /opt/synthea/synthea.jar

COPY generate.sh /opt/synthea/generate.sh
RUN chmod +x /opt/synthea/generate.sh

WORKDIR /opt/synthea
ENTRYPOINT ["/opt/synthea/generate.sh"]
