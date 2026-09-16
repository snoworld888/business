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
-- Table structure for table `tenant`
--

DROP TABLE IF EXISTS `tenant`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `tenant` (
  `id` varchar(32) NOT NULL,
  `create_time` bigint DEFAULT NULL,
  `create_date` datetime DEFAULT NULL,
  `update_time` bigint DEFAULT NULL,
  `update_date` datetime DEFAULT NULL,
  `name` varchar(100) DEFAULT NULL,
  `public_key` varchar(255) DEFAULT NULL,
  `llm_id` varchar(128) NOT NULL,
  `tenant_llm_id` int DEFAULT NULL,
  `embd_id` varchar(128) NOT NULL,
  `tenant_embd_id` int DEFAULT NULL,
  `asr_id` varchar(128) NOT NULL,
  `tenant_asr_id` int DEFAULT NULL,
  `img2txt_id` varchar(128) NOT NULL,
  `tenant_img2txt_id` int DEFAULT NULL,
  `rerank_id` varchar(128) NOT NULL,
  `tenant_rerank_id` int DEFAULT NULL,
  `tts_id` varchar(256) DEFAULT NULL,
  `tenant_tts_id` int DEFAULT NULL,
  `parser_ids` varchar(256) NOT NULL,
  `credit` int NOT NULL,
  `status` varchar(1) DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `tenant_create_time` (`create_time`),
  KEY `tenant_create_date` (`create_date`),
  KEY `tenant_update_time` (`update_time`),
  KEY `tenant_update_date` (`update_date`),
  KEY `tenant_name` (`name`),
  KEY `tenant_public_key` (`public_key`),
  KEY `tenant_llm_id` (`llm_id`),
  KEY `tenant_tenant_llm_id` (`tenant_llm_id`),
  KEY `tenant_embd_id` (`embd_id`),
  KEY `tenant_tenant_embd_id` (`tenant_embd_id`),
  KEY `tenant_asr_id` (`asr_id`),
  KEY `tenant_tenant_asr_id` (`tenant_asr_id`),
  KEY `tenant_img2txt_id` (`img2txt_id`),
  KEY `tenant_tenant_img2txt_id` (`tenant_img2txt_id`),
  KEY `tenant_rerank_id` (`rerank_id`),
  KEY `tenant_tenant_rerank_id` (`tenant_rerank_id`),
  KEY `tenant_tts_id` (`tts_id`),
  KEY `tenant_tenant_tts_id` (`tenant_tts_id`),
  KEY `tenant_parser_ids` (`parser_ids`),
  KEY `tenant_credit` (`credit`),
  KEY `tenant_status` (`status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `tenant`
--

LOCK TABLES `tenant` WRITE;
/*!40000 ALTER TABLE `tenant` DISABLE KEYS */;
INSERT INTO `tenant` VALUES ('617ebec67c0d11f18d050ddef2e4d236',1783653233791,'2026-07-10 11:13:53',1789018615877,'2026-09-10 13:36:55','wxc‘s Kingdom',NULL,'qwen3.5:35b@Ollama',2,'bge-m3:latest@Ollama',1,'',NULL,'',NULL,'',NULL,'',NULL,'naive:General,qa:Q&A,resume:Resume,manual:Manual,table:Table,paper:Paper,book:Book,laws:Laws,presentation:Presentation,picture:Picture,one:One,audio:Audio,email:Email,tag:Tag',512,'1'),('f76bd5647c0c11f19daa1f429e4da610',1783653055848,'2026-07-10 11:10:55',1789018615877,'2026-09-10 13:36:55','admin‘s Kingdom',NULL,'',NULL,'',NULL,'',NULL,'',NULL,'',NULL,NULL,NULL,'naive:General,qa:Q&A,resume:Resume,manual:Manual,table:Table,paper:Paper,book:Book,laws:Laws,presentation:Presentation,picture:Picture,one:One,audio:Audio,email:Email,tag:Tag',512,'1');
/*!40000 ALTER TABLE `tenant` ENABLE KEYS */;
UNLOCK TABLES;
/*!40103 SET TIME_ZONE=@OLD_TIME_ZONE */;

/*!40101 SET SQL_MODE=@OLD_SQL_MODE */;
/*!40014 SET FOREIGN_KEY_CHECKS=@OLD_FOREIGN_KEY_CHECKS */;
/*!40014 SET UNIQUE_CHECKS=@OLD_UNIQUE_CHECKS */;
/*!40101 SET CHARACTER_SET_CLIENT=@OLD_CHARACTER_SET_CLIENT */;
/*!40101 SET CHARACTER_SET_RESULTS=@OLD_CHARACTER_SET_RESULTS */;
/*!40101 SET COLLATION_CONNECTION=@OLD_COLLATION_CONNECTION */;
/*!40111 SET SQL_NOTES=@OLD_SQL_NOTES */;

-- Dump completed on 2026-09-14 16:24:34
