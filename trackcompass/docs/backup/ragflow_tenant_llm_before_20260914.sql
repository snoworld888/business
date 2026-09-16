-- MySQL dump 10.13  Distrib 8.0.39, for Linux (x86_64)
--
-- Host: localhost    Database: rag_flow
-- ------------------------------------------------------
-- Server version	8.0.39

/*!40101 SET @OLD_CHARACTER_SET_CLIENT=@@CHARACTER_SET_CLIENT */;
/*!40101 SET @OLD_CHARACTER_SET_RESULTS=@@CHARACTER_SET_RESULTS */;
/*!40101 SET @OLD_COLLATION_CONNECTION=@@COLLATION_CONNECTION */;
/*!50503 SET NAMES utf8mb4 */;
/*!40103 SET @OLD_TIME_ZONE=@@TIME_ZONE */;
/*!40103 SET TIME_ZONE='+00:00' */;
/*!40014 SET @OLD_UNIQUE_CHECKS=@@UNIQUE_CHECKS, UNIQUE_CHECKS=0 */;
/*!40014 SET @OLD_FOREIGN_KEY_CHECKS=@@FOREIGN_KEY_CHECKS, FOREIGN_KEY_CHECKS=0 */;
/*!40101 SET @OLD_SQL_MODE=@@SQL_MODE, SQL_MODE='NO_AUTO_VALUE_ON_ZERO' */;
/*!40111 SET @OLD_SQL_NOTES=@@SQL_NOTES, SQL_NOTES=0 */;

--
-- Table structure for table `tenant_llm`
--

DROP TABLE IF EXISTS `tenant_llm`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `tenant_llm` (
  `id` int NOT NULL AUTO_INCREMENT,
  `create_time` bigint DEFAULT NULL,
  `create_date` datetime DEFAULT NULL,
  `update_time` bigint DEFAULT NULL,
  `update_date` datetime DEFAULT NULL,
  `tenant_id` varchar(32) NOT NULL,
  `llm_factory` varchar(128) NOT NULL,
  `model_type` varchar(128) DEFAULT NULL,
  `llm_name` varchar(128) DEFAULT NULL,
  `api_key` text,
  `api_base` varchar(255) DEFAULT NULL,
  `max_tokens` int NOT NULL,
  `used_tokens` int NOT NULL,
  `status` varchar(1) NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `tenantllm_tenant_id_llm_factory_llm_name` (`tenant_id`,`llm_factory`,`llm_name`),
  KEY `tenantllm_create_time` (`create_time`),
  KEY `tenantllm_create_date` (`create_date`),
  KEY `tenantllm_update_time` (`update_time`),
  KEY `tenantllm_update_date` (`update_date`),
  KEY `tenantllm_tenant_id` (`tenant_id`),
  KEY `tenantllm_llm_factory` (`llm_factory`),
  KEY `tenantllm_model_type` (`model_type`),
  KEY `tenantllm_llm_name` (`llm_name`),
  KEY `tenantllm_max_tokens` (`max_tokens`),
  KEY `tenantllm_used_tokens` (`used_tokens`),
  KEY `tenantllm_status` (`status`)
) ENGINE=InnoDB AUTO_INCREMENT=4 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `tenant_llm`
--

LOCK TABLES `tenant_llm` WRITE;
/*!40000 ALTER TABLE `tenant_llm` DISABLE KEYS */;
INSERT INTO `tenant_llm` VALUES (1,1783676441828,'2026-07-10 17:40:41',1789374318053,'2026-09-14 16:25:18','617ebec67c0d11f18d050ddef2e4d236','Ollama','embedding','bge-m3:latest','ollama','http://172.25.252.110:11434',8192,29230976,'1'),(3,1787309080245,'2026-08-21 18:44:40',1789374315344,'2026-09-14 16:25:15','617ebec67c0d11f18d050ddef2e4d236','Ollama','chat','qwen2.5:7b','x','http://172.25.252.110:11434',8192,117,'1');
/*!40000 ALTER TABLE `tenant_llm` ENABLE KEYS */;
UNLOCK TABLES;
/*!40103 SET TIME_ZONE=@OLD_TIME_ZONE */;

/*!40101 SET SQL_MODE=@OLD_SQL_MODE */;
/*!40014 SET FOREIGN_KEY_CHECKS=@OLD_FOREIGN_KEY_CHECKS */;
/*!40014 SET UNIQUE_CHECKS=@OLD_UNIQUE_CHECKS */;
/*!40101 SET CHARACTER_SET_CLIENT=@OLD_CHARACTER_SET_CLIENT */;
/*!40101 SET CHARACTER_SET_RESULTS=@OLD_CHARACTER_SET_RESULTS */;
/*!40101 SET COLLATION_CONNECTION=@OLD_COLLATION_CONNECTION */;
/*!40111 SET SQL_NOTES=@OLD_SQL_NOTES */;

-- Dump completed on 2026-09-14 16:26:27
